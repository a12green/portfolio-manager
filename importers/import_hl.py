from __future__ import annotations

from config import OWNER_A
import csv
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ledger.money import parse_money_scalar
from ledger.trades_db import create_fund, get_fund_by_name, init_trade_schema, insert_trade

DEFAULT_FUND_NAME = "UK Super"
DEFAULT_OWNER = OWNER_A
DEFAULT_CURRENCY = "GBP"

# HL description substring -> ticker (more specific names first)
_HL_DESCRIPTION_TICKERS: tuple[tuple[str, str], ...] = (
    ("Taiwan Semiconductor", "TSM"),
    ("NVIDIA", "NVDA"),
    ("Apple Inc", "AAPL"),
    ("CoreWeave", "CRWV"),
    ("Liberty Media", "FWONK"),
    ("Formula One", "FWONK"),
)

# HL simplified export (Date, Action, Share, Qty, Price, Fees)
_HL_SHARE_TICKERS: dict[str, str] = {
    "apple": "AAPL",
    "nvidia": "NVDA",
    "formula1": "FWONK",
    "formula one": "FWONK",
    "tsm": "TSM",
    "coreweave": "CRWV",
}


def hl_ticker_from_description(description: str) -> str | None:
    return _ticker_from_description(description)

_HL_FILE_PROFILES: tuple[tuple[str, str, str], ...] = (
    ("HL", "UK Super", OWNER_A),
)


def resolve_hl_profile(
    path: Path,
    *,
    fund_name: str | None = None,
    owner: str | None = None,
) -> tuple[str, str]:
    if fund_name and owner:
        return fund_name.strip(), owner.strip()
    stem = path.stem.upper()
    for token, name, fund_owner in _HL_FILE_PROFILES:
        if token in stem:
            return name, fund_owner
    parent = path.parent.name.upper()
    if parent in ("AG", "HL"):
        return "UK Super", OWNER_A
    return (fund_name or DEFAULT_FUND_NAME).strip(), (owner or DEFAULT_OWNER).strip()


@dataclass(frozen=True)
class ParsedHlRow:
    trade_date: date
    ticker: str
    side: str
    shares: float
    price: float
    fees: float
    notes: str


def _parse_uk_date(raw: str) -> date:
    s = raw.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    parts = s.split("/")
    if len(parts) == 3:
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        if y < 100:
            y += 2000 if y < 70 else 1900
        return date(y, m, d)
    raise ValueError(f"Could not parse HL date: {raw!r}")


def _parse_hl_summary_date(raw: str) -> date:
    s = raw.strip()
    for fmt in ("%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not parse HL summary date: {raw!r}")


def _is_hl_summary_export(fieldnames: Sequence[str] | None) -> bool:
    if not fieldnames:
        return False
    keys = {str(name).strip().lower() for name in fieldnames}
    return {"date", "action", "share", "qty"}.issubset(keys)


def _ticker_from_share_name(share: str) -> str | None:
    return _HL_SHARE_TICKERS.get(share.strip().lower())


def _gbp_usd_rates_for_dates(dates: Sequence[date]) -> dict[date, float]:
    if not dates:
        return {}
    fallback = 1.27
    try:
        import yfinance as yf
    except ImportError:
        return {d: fallback for d in dates}

    start = min(dates) - timedelta(days=14)
    end = max(dates) + timedelta(days=1)
    try:
        hist = yf.download(
            "GBPUSD=X",
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            progress=False,
        )
    except Exception:
        return {d: fallback for d in dates}

    if hist is None or hist.empty:
        return {d: fallback for d in dates}

    close = hist["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    series: dict[date, float] = {}
    for idx, value in close.items():
        day = idx.date() if hasattr(idx, "date") else idx
        if value == value and float(value) > 0:
            series[day] = float(value)

    if not series:
        return {d: fallback for d in dates}

    sorted_days = sorted(series)
    out: dict[date, float] = {}
    last = series[sorted_days[0]]
    for d in sorted(set(dates)):
        for day in sorted_days:
            if day <= d:
                last = series[day]
            else:
                break
        out[d] = last
    return out


def _usd_to_gbp(usd: float, trade_date: date, rates: dict[date, float]) -> float:
    gbp_usd = rates.get(trade_date, 1.27)
    if gbp_usd <= 0:
        gbp_usd = 1.27
    return usd / gbp_usd


def _fix_summary_year_typos(rows: list[ParsedHlRow]) -> list[ParsedHlRow]:
    """Correct obvious year typos in consolidated HL exports (e.g. May 2025 → 2026)."""
    fixed: list[ParsedHlRow] = []
    for row in rows:
        trade_date = row.trade_date
        if (
            trade_date.year == 2025
            and trade_date.month == 5
            and row.ticker == "NVDA"
            and row.shares in (445.0, 450.0)
        ):
            trade_date = trade_date.replace(year=2026)
        fixed.append(
            ParsedHlRow(
                trade_date=trade_date,
                ticker=row.ticker,
                side=row.side,
                shares=row.shares,
                price=row.price,
                fees=row.fees,
                notes=row.notes,
            )
        )
    return fixed


def _float_cell(raw: str | None) -> float:
    if raw is None or str(raw).strip().lower() in ("", "n/a", "--"):
        return 0.0
    cleaned = str(raw).strip().replace("£", "").replace("GBP", "").strip()
    v = parse_money_scalar(cleaned)
    import math

    return 0.0 if math.isnan(v) else float(v)


def _row_get(row: dict[str, str], *keys: str) -> str:
    for k in keys:
        if k in row and row[k] is not None:
            return str(row[k])
    return ""


def _ticker_from_description(description: str) -> str | None:
    desc = description.strip()
    for needle, ticker in _HL_DESCRIPTION_TICKERS:
        if needle.lower() in desc.lower():
            return ticker
    return None


def _is_hl_trade_reference(ref: str) -> bool:
    return len(ref) >= 2 and ref[0] in ("B", "S") and ref[1].isdigit()


def _parse_hl_row(row: dict[str, str]) -> ParsedHlRow | None:
    ref = _row_get(row, "Reference").strip()
    if not _is_hl_trade_reference(ref):
        return None

    side = "buy" if ref[0] == "B" else "sell"
    desc = _row_get(row, "Description").strip()
    ticker = _ticker_from_description(desc)
    if not ticker:
        return None

    qty = _float_cell(_row_get(row, "Quantity"))
    if qty <= 0:
        return None

    value = _float_cell(_row_get(row, "Value (£)", "Value"))
    unit_p = _float_cell(_row_get(row, "Unit cost (p)", "Unit cost"))

    price = 0.0
    if value != 0:
        price = abs(value) / qty
    elif unit_p > 0:
        price = unit_p / 100.0

    if price <= 0:
        return None

    trade_date = _parse_uk_date(_row_get(row, "Trade date", "Trade Date"))
    clean_desc = re.sub(r"\s+\d+\s*@\s*[\d.]+\s*$", "", desc).strip()
    notes = f"{side.upper()} {clean_desc}"

    return ParsedHlRow(
        trade_date=trade_date,
        ticker=ticker,
        side=side,
        shares=qty,
        price=price,
        fees=0.0,
        notes=notes[:500],
    )


def _parse_hl_summary_row(row: dict[str, str]) -> ParsedHlRow | None:
    action = _row_get(row, "Action").strip().lower()
    if action in ("buy", "bought") or action.startswith("buy"):
        side = "buy"
    elif action in ("sell", "sold") or action.startswith("sell"):
        side = "sell"
    else:
        return None

    share = _row_get(row, "Share").strip()
    ticker = _ticker_from_share_name(share)
    if not ticker:
        return None

    qty = _float_cell(_row_get(row, "Qty", "Quantity"))
    if qty <= 0:
        return None

    price_usd = _float_cell(_row_get(row, "Price"))
    if price_usd <= 0:
        return None

    fees = _float_cell(_row_get(row, "Fees", "Fee"))
    trade_date = _parse_hl_summary_date(_row_get(row, "Date"))
    notes = f"{side.upper()} {share}"

    return ParsedHlRow(
        trade_date=trade_date,
        ticker=ticker,
        side=side,
        shares=qty,
        price=price_usd,
        fees=fees,
        notes=notes[:500],
    )


def _opening_cash_from_rows(rows: list[dict[str, str]]) -> float:
    best = 0.0
    for row in rows:
        ref = _row_get(row, "Reference").strip().upper()
        desc = _row_get(row, "Description").lower()
        if ref != "BACS" and "sipp transfer" not in desc and "transfer" not in desc:
            continue
        val = _float_cell(_row_get(row, "Value (£)", "Value"))
        if val > best:
            best = val
    return best


def load_hl_rows(path: Path) -> tuple[list[ParsedHlRow], float]:
    path = path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"HL CSV not found: {path.resolve()}")

    raw_rows: list[dict[str, str]] = []
    parsed: list[ParsedHlRow] = []
    seen: set[tuple[date, str, str, float, float]] = set()
    summary_export = False

    for encoding in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            with path.open(newline="", encoding=encoding) as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    raise ValueError("CSV has no header row")
                summary_export = _is_hl_summary_export(reader.fieldnames)
                for row in reader:
                    raw_rows.append(row)
                    p = (
                        _parse_hl_summary_row(row)
                        if summary_export
                        else _parse_hl_row(row)
                    )
                    if p is None:
                        continue
                    key = (p.trade_date, p.ticker, p.side, round(p.shares, 6), round(p.price, 4))
                    if key in seen:
                        continue
                    seen.add(key)
                    parsed.append(p)
            break
        except UnicodeDecodeError:
            raw_rows.clear()
            parsed.clear()
            seen.clear()
            continue
    else:
        raise ValueError(f"Could not decode HL CSV: {path}")

    if summary_export:
        parsed = _fix_summary_year_typos(parsed)
        fx_dates = [p.trade_date for p in parsed]
        fx_rates = _gbp_usd_rates_for_dates(fx_dates)
        converted: list[ParsedHlRow] = []
        for p in parsed:
            converted.append(
                ParsedHlRow(
                    trade_date=p.trade_date,
                    ticker=p.ticker,
                    side=p.side,
                    shares=p.shares,
                    price=_usd_to_gbp(p.price, p.trade_date, fx_rates),
                    fees=p.fees,
                    notes=p.notes,
                )
            )
        parsed = converted

    parsed.sort(key=lambda r: (r.trade_date, r.ticker, r.side))
    opening = 0.0 if summary_export else _opening_cash_from_rows(raw_rows)
    return parsed, opening


def import_hl_csv(
    conn: sqlite3.Connection,
    path: Path,
    *,
    fund_name: str | None = None,
    owner: str | None = None,
    currency: str = DEFAULT_CURRENCY,
    replace_existing: bool = False,
) -> int:
    """Import Hargreaves Lansdown transaction history into the trades table."""
    init_trade_schema(conn)
    fname, fowner = resolve_hl_profile(path, fund_name=fund_name, owner=owner)
    rows, opening_cash = load_hl_rows(path)

    fund = get_fund_by_name(conn, fname)
    if fund is None:
        fund = create_fund(conn, fname, fowner, opening_cash, currency)
    elif opening_cash > 0 and fund.opening_cash == 0:
        conn.execute(
            "UPDATE trade_funds SET opening_cash = ? WHERE id = ?",
            (opening_cash, fund.id),
        )
        conn.commit()

    if replace_existing:
        conn.execute("DELETE FROM trades WHERE fund_id = ?", (fund.id,))
        conn.commit()

    for r in rows:
        insert_trade(
            conn,
            fund.id,
            r.trade_date,
            r.ticker,
            r.side,
            r.shares,
            r.price,
            r.fees,
            r.notes,
        )

    return len(rows)
