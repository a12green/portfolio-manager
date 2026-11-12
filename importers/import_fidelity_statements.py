from __future__ import annotations

from config import OWNER_A, OWNER_B
import csv
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ledger.money import parse_money_scalar
from ledger.trades_db import (
    create_fund,
    delete_fund_performance_snapshots,
    get_fund_by_name,
    init_trade_schema,
    upsert_fund_performance_snapshot,
)

try:
    import fitz  # pymupdf
except ImportError:  # pragma: no cover
    fitz = None

# Fidelity statement account -> (fund name, owner)
STATEMENT_ACCOUNT_MAP: dict[str, tuple[str, str]] = {
    "Health Savings Account": ("Fidelity HSA", OWNER_A),
    "Individual - TOD": ("Fidelity", OWNER_A),
    "Limited Liability Company": ("Fidelity", OWNER_A),
    "Traditional IRA": ("Fidelity SG IRA", OWNER_A),
}

# Account-holder line and account number for the owner-B individual account.
# Set these in .env (never commit real values); the defaults are placeholders.
_OWNER_B_HOLDER = os.getenv("FIDELITY_OWNER_B_HOLDER", "HOLDER B - INDIVIDUAL").strip()
_OWNER_B_ACCOUNT = os.getenv("FIDELITY_OWNER_B_ACCOUNT", "").strip()
_KG_STATEMENT_MARKERS = tuple(m for m in (_OWNER_B_HOLDER, _OWNER_B_ACCOUNT) if m)
_ACCOUNT_PATTERN = re.escape(_OWNER_B_ACCOUNT) if _OWNER_B_ACCOUNT else r"[A-Z0-9]{2,}-\d+"

_FIDELITY_FUNDS = frozenset(
    {"Fidelity", "Fidelity HSA", "Fidelity SG IRA", "Fidelity Account B"}
)

# Account numbers seen in AG exports (for position-detail blocks).
_ACCOUNT_ID_RE = re.compile(r"^(Z\d{8}|\d{9})$")
_SUBTOTAL_STOCKS = "Subtotal of Stocks"
_SUBTOTAL_CORE = "Subtotal of Core Account"


@dataclass(frozen=True)
class StatementAccountSummary:
    account_type: str
    account_id: str
    ending_nav: float


@dataclass(frozen=True)
class StatementAccountDetail:
    account_id: str
    stocks_value: float
    cash_value: float


@dataclass(frozen=True)
class ParsedStatementSnapshot:
    snapshot_date: date
    fund_name: str
    owner: str
    nav: float
    cash_balance: float
    positions_value: float
    source_file: str


def parse_statement_filename(path: Path) -> date:
    """Parse month-end date from Statement4302025.csv or HSAStatement10312025.csv."""
    stem = path.stem
    for prefix in ("HSAStatement", "IRAStatement", "Statement"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    if len(stem) < 5:
        raise ValueError(f"Cannot parse statement date from {path.name}")

    year = int(stem[-4:])
    rest = stem[:-4]
    if len(rest) == 3:
        month = int(rest[0])
        day = int(rest[1:])
    elif len(rest) == 4:
        month = int(rest[:2])
        day = int(rest[2:])
    else:
        raise ValueError(f"Cannot parse statement date from {path.name}")
    return date(year, month, day)


def _parse_money_match(match: re.Match[str] | None) -> float | None:
    if match is None:
        return None
    val = parse_money_scalar(match.group(1))
    return float(val) if val is not None else None


def _read_pdf_text(path: Path) -> str:
    if fitz is None:
        raise ImportError("pymupdf is required for PDF statement import (pip install pymupdf)")
    path = path.expanduser()
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _is_kg_statement(text: str, path: Path) -> bool:
    if any(marker in text for marker in _KG_STATEMENT_MARKERS):
        return True
    return path.parent.name.upper() == "ACCOUNT_B"


def parse_fidelity_statement_pdf(path: Path) -> list[ParsedStatementSnapshot]:
    """Parse monthly Fidelity brokerage statement PDF (owner B individual account)."""
    path = path.expanduser()
    text = _read_pdf_text(path)
    if not _is_kg_statement(text, path):
        return []

    snapshot_date = parse_statement_filename(path)
    fund_name, owner = "Fidelity Account B", OWNER_B

    nav = _parse_money_match(re.search(r"Total Holdings\s*\n\$?\s*([\d,]+\.\d{2})", text))
    if nav is None:
        nav = _parse_money_match(re.search(r"Your Account Value:\s*\n?\$?\s*([\d,]+\.\d{2})", text))
    if nav is None:
        nav = _parse_money_match(
            re.search(_ACCOUNT_PATTERN + r"\s*\n\$[\d,]+\.\d{2}\s*\n\$?\s*([\d,]+\.\d{2})", text)
        )
    if nav is None or nav <= 0:
        return []

    cash = _parse_money_match(
        re.search(
            r"Total Core Account[^\$]*\(\d+%[^\n]*\n\$[\d,]+\.\d{2}\s*\n\$([\d,]+\.\d{2})",
            text,
            flags=re.S,
        )
    )
    positions = _parse_money_match(
        re.search(
            r"Total Stocks \(\d+% of account holdings\)\s*\n\$[\d,]+\.\d{2}\s*\n\$([\d,]+\.\d{2})",
            text,
        )
    )
    if cash is None:
        cash = _parse_money_match(re.search(r"Free Credit Balance\s*\n\$?\s*([\d,]+\.\d{2})", text))
    if positions is None and cash is not None:
        positions = max(0.0, nav - cash)
    elif positions is None:
        positions = nav
    if cash is None:
        cash = max(0.0, nav - positions)

    return [
        ParsedStatementSnapshot(
            snapshot_date=snapshot_date,
            fund_name=fund_name,
            owner=owner,
            nav=nav,
            cash_balance=cash,
            positions_value=positions,
            source_file=path.name,
        )
    ]


def _parse_summary_rows(lines: list[str]) -> list[StatementAccountSummary]:
    out: list[StatementAccountSummary] = []
    reader = csv.reader(lines)
    for row in reader:
        if not row or not str(row[0]).strip():
            continue
        if row[0].strip() == "Account Type":
            continue
        if row[0].strip().startswith("Symbol"):
            break
        account_type = str(row[0]).strip()
        if account_type not in STATEMENT_ACCOUNT_MAP:
            continue
        if len(row) < 5:
            continue
        account_id = str(row[1]).strip()
        ending = parse_money_scalar(row[4])
        if ending is None:
            continue
        out.append(
            StatementAccountSummary(
                account_type=account_type,
                account_id=account_id,
                ending_nav=float(ending),
            )
        )
    return out


def _ending_value_from_subtotal_row(row: list[str]) -> float | None:
    for cell in row[4:6]:
        val = parse_money_scalar(cell)
        if val is not None and float(val) >= 0:
            return float(val)
    return None


def _parse_detail_blocks(lines: list[str]) -> dict[str, StatementAccountDetail]:
    details: dict[str, StatementAccountDetail] = {}
    current_id: str | None = None
    section: str | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if _ACCOUNT_ID_RE.match(line.split(",")[0].strip()):
            current_id = line.split(",")[0].strip()
            section = None
            details.setdefault(
                current_id,
                StatementAccountDetail(account_id=current_id, stocks_value=0.0, cash_value=0.0),
            )
            continue
        if current_id is None:
            continue
        first = line.split(",")[0].strip()
        if first == "Stocks":
            section = "stocks"
            continue
        if first == "Core Account":
            section = "core"
            continue
        if first == _SUBTOTAL_STOCKS and section == "stocks":
            row = next(csv.reader([line]))
            val = _ending_value_from_subtotal_row(row)
            if val is not None:
                prev = details[current_id]
                details[current_id] = StatementAccountDetail(
                    account_id=current_id,
                    stocks_value=val,
                    cash_value=prev.cash_value,
                )
            section = None
            continue
        if first == _SUBTOTAL_CORE and section == "core":
            row = next(csv.reader([line]))
            val = _ending_value_from_subtotal_row(row)
            if val is not None:
                prev = details[current_id]
                details[current_id] = StatementAccountDetail(
                    account_id=current_id,
                    stocks_value=prev.stocks_value,
                    cash_value=val,
                )
            section = None
    return details


def _pick_fidelity_main_account(
    summaries: list[StatementAccountSummary],
) -> StatementAccountSummary | None:
    llc = next((s for s in summaries if s.account_type == "Limited Liability Company"), None)
    ind = next((s for s in summaries if s.account_type == "Individual - TOD"), None)
    if llc is not None and llc.ending_nav > 1000:
        return llc
    if ind is not None and ind.ending_nav > 1000:
        return ind
    if llc is not None and llc.ending_nav > 0:
        return llc
    if ind is not None and ind.ending_nav > 0:
        return ind
    return None


def parse_fidelity_statement(path: Path) -> list[ParsedStatementSnapshot]:
    path = path.expanduser()
    if path.suffix.lower() == ".pdf":
        return parse_fidelity_statement_pdf(path)

    snapshot_date = parse_statement_filename(path)
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    summaries = _parse_summary_rows(lines)
    details = _parse_detail_blocks(lines)
    out: list[ParsedStatementSnapshot] = []

    main = _pick_fidelity_main_account(summaries)
    if main is not None:
        detail = details.get(main.account_id)
        positions = detail.stocks_value if detail else 0.0
        cash = detail.cash_value if detail else 0.0
        nav = main.ending_nav
        if positions + cash <= 0 and nav > 0:
            positions = nav
        elif abs((positions + cash) - nav) > 1.0 and nav > 0:
            positions = max(0.0, nav - cash)
        fund_name, owner = STATEMENT_ACCOUNT_MAP[main.account_type]
        out.append(
            ParsedStatementSnapshot(
                snapshot_date=snapshot_date,
                fund_name=fund_name,
                owner=owner,
                nav=nav,
                cash_balance=cash,
                positions_value=positions if positions > 0 else max(0.0, nav - cash),
                source_file=path.name,
            )
        )

    for summary in summaries:
        if summary.account_type in ("Individual - TOD", "Limited Liability Company"):
            continue
        fund_name, owner = STATEMENT_ACCOUNT_MAP[summary.account_type]
        if summary.ending_nav <= 0:
            continue
        detail = details.get(summary.account_id)
        positions = detail.stocks_value if detail else 0.0
        cash = detail.cash_value if detail else 0.0
        nav = summary.ending_nav
        if positions + cash <= 0 and nav > 0:
            positions = nav
        elif abs((positions + cash) - nav) > 1.0 and nav > 0:
            positions = max(0.0, nav - cash)
        out.append(
            ParsedStatementSnapshot(
                snapshot_date=snapshot_date,
                fund_name=fund_name,
                owner=owner,
                nav=nav,
                cash_balance=cash,
                positions_value=positions if positions > 0 else max(0.0, nav - cash),
                source_file=path.name,
            )
        )
    return out


def _ensure_fund(conn: sqlite3.Connection, fund_name: str, owner: str) -> int:
    fund = get_fund_by_name(conn, fund_name)
    if fund is None:
        fund = create_fund(conn, fund_name, owner, opening_cash=0.0, currency="USD")
    return fund.id


def import_fidelity_statements(
    conn: sqlite3.Connection,
    paths: list[Path],
    *,
    replace: bool = True,
) -> dict[str, int]:
    """Import monthly Fidelity statement NAV snapshots."""
    init_trade_schema(conn)
    parsed: list[ParsedStatementSnapshot] = []
    for path in paths:
        path = path.expanduser()
        if not path.is_file():
            raise FileNotFoundError(path)
        parsed.extend(parse_fidelity_statement(path))

    if replace:
        touched = {snap.fund_name for snap in parsed}
        for fund_name in touched:
            fund = get_fund_by_name(conn, fund_name)
            if fund is not None:
                delete_fund_performance_snapshots(conn, fund.id)

    # Latest file wins when the same fund/date appears twice.
    by_key: dict[tuple[str, date], ParsedStatementSnapshot] = {}
    for snap in parsed:
        by_key[(snap.fund_name, snap.snapshot_date)] = snap

    counts: dict[str, int] = {}
    for snap in sorted(by_key.values(), key=lambda s: (s.fund_name, s.snapshot_date)):
        fund_id = _ensure_fund(conn, snap.fund_name, snap.owner)
        upsert_fund_performance_snapshot(
            conn,
            snap.snapshot_date,
            fund_id,
            cash_balance=snap.cash_balance,
            positions_value=snap.positions_value,
            nav=snap.nav,
            invested_capital=0.0,
            cumulative_realized_pl=0.0,
            roi_pct=None,
        )
        counts[snap.fund_name] = counts.get(snap.fund_name, 0) + 1
    return counts


def collect_statement_paths(paths: list[Path]) -> list[Path]:
    """Expand directories and dedupe statement CSV/PDF paths."""
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        raw = raw.expanduser()
        if raw.is_dir():
            candidates = (
                sorted(raw.glob("**/Statement*.csv"))
                + sorted(raw.glob("**/Statement*.pdf"))
                + sorted(raw.glob("**/HSAStatement*.csv"))
                + sorted(raw.glob("**/HSAStatement*.pdf"))
                + sorted(raw.glob("**/IRAStatement*.csv"))
                + sorted(raw.glob("**/IRAStatement*.pdf"))
            )
        else:
            candidates = [raw]
        for path in candidates:
            resolved = path.resolve()
            if resolved in seen:
                continue
            if "Statement" not in path.stem:
                continue
            seen.add(resolved)
            out.append(path)
    return sorted(out, key=lambda p: (parse_statement_filename(p), p.name))
