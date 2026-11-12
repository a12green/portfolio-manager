from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from importers.import_aussuper import DEFAULT_CURRENCY, DEFAULT_FUND_NAME, DEFAULT_OWNER, resolve_aussuper_profile
from ledger.trades_db import (
    create_fund,
    delete_fund_performance_snapshots,
    get_fund_by_name,
    init_trade_schema,
    upsert_fund_performance_snapshot,
)

try:
    import fitz  # pymupdf
except ImportError as exc:  # pragma: no cover
    raise ImportError("pymupdf is required for Australian Super PDF import (pip install pymupdf)") from exc

_MONTHS: dict[str, int] = {
    name: idx
    for idx, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}


@dataclass(frozen=True)
class AusSuperStatementSnapshot:
    snapshot_date: date
    fund_name: str
    owner: str
    nav: float
    cash_balance: float
    positions_value: float
    source_file: str


def _parse_money(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _read_pdf_text(path: Path) -> str:
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _snapshot_date_from_filename(stem: str) -> date | None:
    match = re.search(r"(\d{4})JUNST", stem.upper())
    if match:
        return date(int(match.group(1)), 6, 30)
    return None


def _snapshot_date_from_text(text: str) -> date | None:
    match = re.search(
        r"Closing balance at (\d{1,2}) (\w+) (\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        month = _MONTHS.get(match.group(2).title())
        if month:
            return date(int(match.group(3)), month, int(match.group(1)))

    match = re.search(
        r"(?:Annual super statement\s*\n)?(?:for )?1 July \d{4} to 30 June (\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return date(int(match.group(1)), 6, 30)

    match = re.search(
        r"Your super balance at 30 June (\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return date(int(match.group(1)), 6, 30)
    return None


def _closing_balance(text: str, snapshot_date: date | None) -> float | None:
    if snapshot_date is not None:
        match = re.search(
            rf"Closing balance at {snapshot_date.day} \w+ {snapshot_date.year}\s*\n"
            rf"\$?\s*([\d,]+\.\d{{2}})\s*\n\$?\s*([\d,]+\.\d{{2}})",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return _parse_money(match.group(2))

        match = re.search(
            rf"Closing balance at {snapshot_date.day} \w+ {snapshot_date.year}\s*\n\$?\s*([\d,]+\.\d{{2}})",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return _parse_money(match.group(1))

    match = re.search(r"Closing balance\s*\n\$?\s*([\d,]+\.\d{2})", text, flags=re.IGNORECASE)
    if match:
        return _parse_money(match.group(1))

    if snapshot_date is not None:
        match = re.search(
            rf"Your super balance at 30 June {snapshot_date.year}\s*\n\$?\s*([\d,]+\.\d{{2}})",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return _parse_money(match.group(1))

        match = re.search(
            rf"Your super balance at 30 June {snapshot_date.year - 1}.*?Your super balance at 30 June {snapshot_date.year}\s*\n\$?\s*[\d,]+\.\d{{2}}\s*\n\$?\s*([\d,]+\.\d{{2}})",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return _parse_money(match.group(1))
    return None


def _positions_value(text: str) -> float | None:
    idx = text.find("Your investment summary")
    if idx < 0:
        idx = text.find("Your investment returns and strategy")
    block = text[idx : idx + 3500] if idx >= 0 else text

    match = re.search(
        r"\nTotal\s*\n[-\d,]+\.\d+\s*\n([\d,]+\.\d{2})\s*\n100\.00",
        block,
    )
    if match:
        return _parse_money(match.group(1))

    match = re.search(
        r"\nTotal\s*\n[\d,]+\.\d+\s*\n([\d,]+\.\d{2})\s*\n100\.00",
        block,
    )
    if match:
        return _parse_money(match.group(1))
    return None


def parse_aussuper_statement_pdf(
    path: Path,
    *,
    fund_name: str | None = None,
    owner: str | None = None,
) -> AusSuperStatementSnapshot | None:
    path = path.expanduser()
    text = _read_pdf_text(path)
    fname, fowner = resolve_aussuper_profile(path, fund_name=fund_name, owner=owner)

    snapshot_date = _snapshot_date_from_filename(path.stem) or _snapshot_date_from_text(text)
    nav = _closing_balance(text, snapshot_date)
    if nav is None or nav <= 0 or snapshot_date is None:
        return None

    positions = _positions_value(text)
    if positions is None or abs(positions - nav) > nav * 0.05:
        positions = nav
    cash = max(0.0, nav - positions)

    return AusSuperStatementSnapshot(
        snapshot_date=snapshot_date,
        fund_name=fname,
        owner=fowner,
        nav=nav,
        cash_balance=cash,
        positions_value=positions,
        source_file=path.name,
    )


def collect_aussuper_statement_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        raw = raw.expanduser()
        if raw.is_dir():
            candidates = sorted(raw.glob("**/*.pdf"))
        else:
            candidates = [raw]
        for path in candidates:
            if path.suffix.lower() != ".pdf":
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(path)
    return out


def import_aussuper_statements(
    conn: sqlite3.Connection,
    paths: list[Path],
    *,
    fund_name: str | None = None,
    owner: str | None = None,
    currency: str = DEFAULT_CURRENCY,
    replace: bool = True,
) -> dict[str, int]:
    """Import annual Australian Super statement PDFs into fund performance snapshots."""
    init_trade_schema(conn)
    parsed: list[AusSuperStatementSnapshot] = []
    for path in collect_aussuper_statement_paths(paths):
        snap = parse_aussuper_statement_pdf(path, fund_name=fund_name, owner=owner)
        if snap is not None:
            parsed.append(snap)

    if not parsed:
        return {}

    fname = parsed[0].fund_name
    fowner = parsed[0].owner
    fund = get_fund_by_name(conn, fname)
    if fund is None:
        fund = create_fund(conn, fname, fowner, opening_cash=0.0, currency=currency)

    if replace:
        delete_fund_performance_snapshots(conn, fund.id)

    by_date: dict[date, AusSuperStatementSnapshot] = {}
    for snap in parsed:
        by_date[snap.snapshot_date] = snap

    for snap in sorted(by_date.values(), key=lambda s: s.snapshot_date):
        upsert_fund_performance_snapshot(
            conn,
            snap.snapshot_date,
            fund.id,
            cash_balance=snap.cash_balance,
            positions_value=snap.positions_value,
            nav=snap.nav,
            invested_capital=0.0,
            cumulative_realized_pl=0.0,
            roi_pct=None,
        )

    return {fname: len(by_date)}
