from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from importers.import_hl import DEFAULT_CURRENCY, DEFAULT_FUND_NAME, DEFAULT_OWNER, hl_ticker_from_description
from ledger.trades_db import (
    create_fund,
    delete_fund_performance_snapshots,
    delete_fund_ticker_marks,
    get_fund_by_name,
    init_trade_schema,
    upsert_fund_performance_snapshot,
    upsert_fund_ticker_mark,
)

try:
    import fitz  # pymupdf
except ImportError as exc:  # pragma: no cover
    raise ImportError("pymupdf is required for HL report import (pip install pymupdf)") from exc

_MONTHS: dict[str, int] = {
    name: idx
    for idx, name in enumerate(
        [
            "JANUARY",
            "FEBRUARY",
            "MARCH",
            "APRIL",
            "MAY",
            "JUNE",
            "JULY",
            "AUGUST",
            "SEPTEMBER",
            "OCTOBER",
            "NOVEMBER",
            "DECEMBER",
        ],
        start=1,
    )
}

_PRICE_MOVEMENT_NAMES: tuple[tuple[str, str], ...] = (
    ("NVIDIA Corp", "NVDA"),
    ("Liberty Media Corp", "FWONK"),
    ("Taiwan Semiconductor Manufacturing Co", "TSM"),
    ("CoreWeave Inc", "CRWV"),
)


@dataclass(frozen=True)
class HlHoldingSnapshot:
    description: str
    ticker: str | None
    quantity: float
    value_gbp: float
    cost_gbp: float
    price_gbp: float


@dataclass(frozen=True)
class HlReportSnapshot:
    snapshot_date: date
    source_file: str
    holdings: tuple[HlHoldingSnapshot, ...]
    positions_value: float
    capital_cash: float
    income_cash: float
    nav: float
    price_marks: tuple[tuple[date, str, float], ...]


def _money(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s or s == "-":
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def _read_pdf_text(path: Path) -> str:
    path = path.expanduser()
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _parse_snapshot_date(text: str) -> date | None:
    match = re.search(
        r"TOTAL\s+SIPP\s+(\d{1,2})\s+"
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+"
        r"(\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    day = int(match.group(1))
    month = _MONTHS[match.group(2).upper()]
    year = int(match.group(3))
    return date(year, month, day)


def _parse_total_nav(text: str) -> float | None:
    match = re.search(
        r"(?:[\d,]+|\([\d,]+\))\s+([\d,]+|\([\d,]+\))\s+TOTAL\s+SIPP",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return _money(match.group(1))
    match = re.search(r"([\d,]+|\([\d,]+\))\s+TOTAL\s+SIPP", text, flags=re.IGNORECASE)
    if match:
        return _money(match.group(1))
    return None


def _parse_detailed_valuation(text: str) -> tuple[list[HlHoldingSnapshot], float | None, float, float]:
    start = text.find("DETAILED VALUATION")
    if start < 0:
        return [], None, 0.0, 0.0

    end = text.find("CAPITAL ACCOUNT TRANSACTIONS", start)
    block = text[start:end if end >= 0 else start + 5000]
    lines = [line.strip() for line in block.splitlines() if line.strip()]

    holdings: list[HlHoldingSnapshot] = []
    subtotal: float | None = None
    capital_cash = 0.0
    income_cash = 0.0

    idx = 0
    while idx < len(lines) and lines[idx] != "DETAILED VALUATION":
        idx += 1
    idx += 1

    while idx < len(lines):
        if lines[idx] == "Subtotal":
            subtotal = _money(lines[idx + 1]) if idx + 1 < len(lines) else None
            idx += 2
            break
        if idx + 6 >= len(lines):
            break

        qty = _money(lines[idx])
        value = _money(lines[idx + 1])
        cost = _money(lines[idx + 2])
        _gain = _money(lines[idx + 3])
        _pct = _money(lines[idx + 4])
        price_pence = _money(lines[idx + 5])
        description = lines[idx + 6]

        if qty is None or price_pence is None or not description or description.startswith("-"):
            idx += 1
            continue
        if hl_ticker_from_description(description) is None:
            idx += 1
            continue

        holdings.append(
            HlHoldingSnapshot(
                description=description,
                ticker=hl_ticker_from_description(description),
                quantity=qty,
                value_gbp=value or 0.0,
                cost_gbp=cost or 0.0,
                price_gbp=price_pence / 100.0,
            )
        )
        idx += 7

    for j, line in enumerate(lines[idx:], start=idx):
        if line == "Capital Account Balance" and j + 1 < len(lines):
            capital_cash = _money(lines[j + 1]) or 0.0
        if line == "Income Account Balance" and j + 1 < len(lines):
            income_cash = _money(lines[j + 1]) or 0.0

    return holdings, subtotal, capital_cash, income_cash


def _parse_price_movements(text: str, end_date: date) -> list[tuple[date, str, float]]:
    start = text.find("PRICE MOVEMENTS")
    if start < 0:
        return []

    stop = text.find("Please note Investment Trusts", start)
    block = text[start:stop if stop >= 0 else start + 2500]
    flat = " ".join(block.split())
    prior_year = end_date.replace(year=end_date.year - 1)

    out: list[tuple[date, str, float]] = []
    for name, ticker in _PRICE_MOVEMENT_NAMES:
        pos = flat.find(name)
        if pos < 0:
            continue
        rest = flat[pos + len(name) :]
        rest = re.sub(r"USD0\.\d+", "", rest, count=1).strip()
        prices_pence: list[float] = []
        for match in re.finditer(r"([\d,]+\.\d+)", rest[:120]):
            value = float(match.group(1).replace(",", ""))
            if value >= 100:
                prices_pence.append(value)
            if len(prices_pence) >= 2:
                break
        if len(prices_pence) < 2:
            continue
        out.append((prior_year, ticker, prices_pence[0] / 100.0))
        out.append((end_date, ticker, prices_pence[1] / 100.0))
    return out


def parse_hl_investment_report(path: Path) -> HlReportSnapshot | None:
    path = path.expanduser()
    text = _read_pdf_text(path)
    snapshot_date = _parse_snapshot_date(text)
    if snapshot_date is None:
        return None

    holdings, subtotal, capital_cash, income_cash = _parse_detailed_valuation(text)
    positions_value = subtotal if subtotal is not None else sum(h.value_gbp for h in holdings)
    nav = _parse_total_nav(text)
    if nav is None:
        nav = positions_value + capital_cash + income_cash

    marks = _parse_price_movements(text, snapshot_date)
    mark_keys: set[tuple[date, str]] = set()
    merged_marks: list[tuple[date, str, float]] = []
    for holding in holdings:
        if not holding.ticker:
            continue
        merged_marks.append((snapshot_date, holding.ticker, holding.price_gbp))
        mark_keys.add((snapshot_date, holding.ticker))
    for mark_date, ticker, price in marks:
        if price <= 0 or (mark_date, ticker) in mark_keys:
            continue
        merged_marks.append((mark_date, ticker, price))
        mark_keys.add((mark_date, ticker))

    return HlReportSnapshot(
        snapshot_date=snapshot_date,
        source_file=path.name,
        holdings=tuple(holdings),
        positions_value=positions_value,
        capital_cash=capital_cash,
        income_cash=income_cash,
        nav=nav,
        price_marks=tuple(merged_marks),
    )


def collect_hl_report_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        raw = raw.expanduser()
        if raw.is_dir():
            candidates = sorted(raw.glob("*Investment Report.pdf"))
        elif raw.is_file():
            candidates = [raw]
        else:
            raise FileNotFoundError(raw)
        for path in candidates:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                out.append(path)
    return out


def import_hl_investment_reports(
    conn: sqlite3.Connection,
    paths: list[Path],
    *,
    fund_name: str = DEFAULT_FUND_NAME,
    owner: str = DEFAULT_OWNER,
    currency: str = DEFAULT_CURRENCY,
    replace: bool = True,
) -> dict[str, int]:
    """Import quarterly HL investment report valuations and share prices."""
    init_trade_schema(conn)
    parsed: list[HlReportSnapshot] = []
    for path in collect_hl_report_paths(paths):
        snap = parse_hl_investment_report(path)
        if snap is not None:
            parsed.append(snap)

    fund = get_fund_by_name(conn, fund_name)
    if fund is None:
        fund = create_fund(conn, fund_name, owner, 0.0, currency)

    if replace:
        delete_fund_performance_snapshots(conn, fund.id)
        delete_fund_ticker_marks(conn, fund.id)

    by_date: dict[date, HlReportSnapshot] = {}
    for snap in parsed:
        by_date[snap.snapshot_date] = snap

    snapshot_count = 0
    mark_count = 0
    for snap in sorted(by_date.values(), key=lambda s: s.snapshot_date):
        cash = snap.capital_cash + snap.income_cash
        upsert_fund_performance_snapshot(
            conn,
            snap.snapshot_date,
            fund.id,
            cash_balance=cash,
            positions_value=snap.positions_value,
            nav=snap.nav,
            invested_capital=0.0,
            cumulative_realized_pl=0.0,
            roi_pct=None,
        )
        snapshot_count += 1
        for mark_date, ticker, price in snap.price_marks:
            upsert_fund_ticker_mark(
                conn,
                fund.id,
                mark_date,
                ticker,
                price,
                currency=currency,
                source=snap.source_file,
            )
            mark_count += 1

    return {"snapshots": snapshot_count, "marks": mark_count}
