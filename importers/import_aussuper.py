from __future__ import annotations

from config import OWNER_A
import csv
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ledger.money import parse_money_scalar
from ledger.trades_db import (
    create_fund,
    delete_fund_cash_events,
    delete_fund_performance_snapshots,
    get_fund_by_name,
    init_trade_schema,
    insert_fund_cash_event,
    insert_trade,
)

DEFAULT_FUND_NAME = "Super A"
DEFAULT_OWNER = OWNER_A
DEFAULT_CURRENCY = "AUD"

# Filename hints (AusSuperAG.csv / AusSuperSG.csv) -> fund + owner for Fund List alignment
_AUSSUPER_PROFILES: tuple[tuple[str, str, str], ...] = (
    ("AG", "Super A", OWNER_A),
    ("SG", "Super S", OWNER_A),
)


def resolve_aussuper_profile(
    path: Path,
    *,
    fund_name: str | None = None,
    owner: str | None = None,
) -> tuple[str, str]:
    if fund_name and owner:
        return fund_name.strip(), owner.strip()
    stem = path.stem.upper()
    parent = path.parent.name.upper()
    for token, name, fund_owner in _AUSSUPER_PROFILES:
        if token in stem or token in parent or f"AUSTRALIANSUPER{token}" in parent:
            return name, fund_owner
    return (fund_name or DEFAULT_FUND_NAME).strip(), (owner or DEFAULT_OWNER).strip()


@dataclass(frozen=True)
class ParsedAusSuperRow:
    trade_date: date
    ticker: str
    side: str
    shares: float
    price: float
    notes: str


@dataclass(frozen=True)
class ParsedAusSuperCashEvent:
    event_date: date
    amount: float
    notes: str


@dataclass(frozen=True)
class AusSuperImportResult:
    trades: int
    cash_events: int
    snapshots: int
    opening_cash: float


_ALLTX_TRADE_RE = re.compile(
    r"^(?:Buying|Sold)\s+([\d,]+(?:\.\d+)?)\s+([A-Z0-9]+)\.AU\s+@\s+AUD([\d.]+)",
    re.IGNORECASE,
)
_ALLTX_ROC_RE = re.compile(
    r"Return of Capital:\s*([A-Z0-9]+)\.AU",
    re.IGNORECASE,
)
_ALLTX_TAKEOVER_RE = re.compile(
    r"Takeover of\s+([A-Z0-9]+)\.AU\s+@\s+AUD\s+([\d.]+)\s+p/s",
    re.IGNORECASE,
)


def _parse_au_date(raw: str) -> date:
    s = raw.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%m/%Y"):
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
    raise ValueError(f"Could not parse Australian Super date: {raw!r}")


def _float_cell(raw: str | None) -> float:
    if raw is None or str(raw).strip() in ("", "--"):
        return 0.0
    v = parse_money_scalar(raw)
    import math

    return 0.0 if math.isnan(v) else float(v)


def _pct_complete(raw: str) -> float | None:
    s = str(raw or "").strip().replace("%", "")
    if not s or s == "--":
        return None
    return _float_cell(s)


def _row_get(row: dict[str, str], *keys: str) -> str:
    for k in keys:
        if k in row and row[k] is not None:
            return str(row[k])
    return ""


def _is_alltx_export(fieldnames: list[str] | None) -> bool:
    if not fieldnames:
        return False
    keys = {str(name).strip().lower() for name in fieldnames}
    return {"date", "description", "type", "amount", "balance"}.issubset(keys)


def _ticker_from_alltx_symbol(symbol: str) -> str:
    return symbol.strip().upper().removesuffix(".AU")


def _parse_alltx_trade(row: dict[str, str]) -> ParsedAusSuperRow | None:
    tx_type = _row_get(row, "Type").strip().lower()
    if tx_type not in ("buy", "sell"):
        return None
    desc = _row_get(row, "Description").strip()
    match = _ALLTX_TRADE_RE.match(desc)
    if not match:
        return None
    shares = _float_cell(match.group(1))
    ticker = _ticker_from_alltx_symbol(match.group(2))
    price = _float_cell(match.group(3))
    if shares <= 0 or price <= 0 or not ticker:
        return None
    side = "buy" if tx_type == "buy" else "sell"
    trade_date = _parse_au_date(_row_get(row, "Date"))
    return ParsedAusSuperRow(
        trade_date=trade_date,
        ticker=ticker,
        side=side,
        shares=shares,
        price=price,
        notes=desc[:500],
    )


def _parse_alltx_cash_event(row: dict[str, str]) -> ParsedAusSuperCashEvent | None:
    tx_type = _row_get(row, "Type").strip()
    if tx_type.lower() in ("buy", "sell"):
        return None
    amount = _float_cell(_row_get(row, "Amount"))
    if amount == 0:
        return None
    event_date = _parse_au_date(_row_get(row, "Date"))
    desc = _row_get(row, "Description").strip()
    if _ALLTX_TAKEOVER_RE.search(desc):
        return None
    notes = f"{tx_type}: {desc}" if desc else tx_type
    return ParsedAusSuperCashEvent(
        event_date=event_date,
        amount=amount,
        notes=notes[:500],
    )


def _opening_cash_from_alltx_rows(rows: list[dict[str, str]]) -> float:
    for row in rows:
        if _row_get(row, "Type").strip().lower() != "capital in":
            continue
        amount = _float_cell(_row_get(row, "Amount"))
        if amount > 0:
            return amount
    return 0.0


def _net_shares_on_or_before(
    trades: Sequence[ParsedAusSuperRow],
    ticker: str,
    as_of: date,
) -> float:
    net = 0.0
    for tr in trades:
        if tr.ticker != ticker or tr.trade_date > as_of:
            continue
        if tr.side == "buy":
            net += tr.shares
        else:
            net -= tr.shares
    return net


def _avg_cost_on_or_before(
    trades: Sequence[ParsedAusSuperRow],
    ticker: str,
    as_of: date,
) -> float:
    cost = 0.0
    shares = 0.0
    for tr in sorted(trades, key=lambda t: t.trade_date):
        if tr.ticker != ticker or tr.trade_date > as_of:
            continue
        if tr.side == "buy":
            cost += tr.shares * tr.price
            shares += tr.shares
        elif shares > 1e-9:
            avg = cost / shares
            sold = min(tr.shares, shares)
            cost -= sold * avg
            shares -= sold
    if shares <= 1e-9:
        return 0.0
    return cost / shares


def _liquidation_sells_from_alltx(
    raw_rows: list[dict[str, str]],
    trades: list[ParsedAusSuperRow],
) -> list[ParsedAusSuperRow]:
    """
    Return-of-capital rows liquidate the holding without a Sold trade line.
    Insert a sell for remaining shares on the last return-of-capital date per ticker.
    """
    roc_events: dict[str, list[tuple[date, float]]] = {}
    for row in raw_rows:
        if _row_get(row, "Type").strip().lower() != "return of capital":
            continue
        match = _ALLTX_ROC_RE.search(_row_get(row, "Description"))
        if not match:
            continue
        ticker = _ticker_from_alltx_symbol(match.group(1))
        event_date = _parse_au_date(_row_get(row, "Date"))
        amount = _float_cell(_row_get(row, "Amount"))
        roc_events.setdefault(ticker, []).append((event_date, amount))

    sells: list[ParsedAusSuperRow] = []
    for ticker, events in roc_events.items():
        last_date = max(d for d, _ in events)
        net = _net_shares_on_or_before(trades, ticker, last_date)
        if net <= 1e-6:
            continue
        total_roc = sum(amt for _, amt in events)
        price = total_roc / net if net > 0 else 0.0
        if price <= 0:
            price = 0.01
        sells.append(
            ParsedAusSuperRow(
                trade_date=last_date,
                ticker=ticker,
                side="sell",
                shares=net,
                price=price,
                notes=(
                    f"Liquidation: return of capital ({len(events)} payment(s), "
                    f"${total_roc:,.2f})"
                )[:500],
            )
        )
    return sells


def _takeover_sells_from_alltx(
    raw_rows: list[dict[str, str]],
    trades: list[ParsedAusSuperRow],
) -> list[ParsedAusSuperRow]:
    """
    Scheme takeover proceeds appear as Interest rows, not Sold trades.
    Close the open ledger position and skip importing the amount as cash.
    """
    sells: list[ParsedAusSuperRow] = []
    for row in raw_rows:
        desc = _row_get(row, "Description").strip()
        match = _ALLTX_TAKEOVER_RE.search(desc)
        if not match:
            continue
        ticker = _ticker_from_alltx_symbol(match.group(1))
        event_date = _parse_au_date(_row_get(row, "Date"))
        amount = _float_cell(_row_get(row, "Amount"))
        if amount <= 0:
            continue
        net = _net_shares_on_or_before(trades, ticker, event_date)
        if net <= 1e-6:
            continue
        scheme_px = _float_cell(match.group(2))
        price = amount / net if net > 0 else scheme_px
        if price <= 0:
            price = scheme_px if scheme_px > 0 else 0.01
        sells.append(
            ParsedAusSuperRow(
                trade_date=event_date,
                ticker=ticker,
                side="sell",
                shares=net,
                price=price,
                notes=(
                    f"Takeover: {desc} (${amount:,.2f} for {net:g} sh)"
                )[:500],
            )
        )
    return sells


# TPG Telecom (TPM) / Vodafone merger — implementation 13 Jul 2020 (ATO CR 2020/41)
_TPM_MERGER_DATE = date(2020, 7, 13)
_TPG_MERGER_COST = 8.2603  # per new TPG share
_TUA_SPINOFF_COST = 0.6799  # per new TUA share
_TUA_PER_TPM = 0.5  # 1 TUA for every 2 TPM


def _tpm_merger_trades(trades: list[ParsedAusSuperRow]) -> list[ParsedAusSuperRow]:
    """
    TPM delisted 13 Jul 2020: 1 TPM → 1 TPG + 0.5 TUA. Cash dividend is already in alltx.
    """
    merger_date = _TPM_MERGER_DATE
    for tr in trades:
        if (
            tr.trade_date == merger_date
            and tr.ticker == "TPM"
            and tr.side == "sell"
            and "merger" in (tr.notes or "").lower()
        ):
            return []

    tpm_shares = _net_shares_on_or_before(trades, "TPM", merger_date)
    if tpm_shares <= 1e-6:
        return []

    tua_shares = round(tpm_shares * _TUA_PER_TPM, 6)
    note = "TPM→TPG/TUA merger (13 Jul 2020, 1 TPM = 1 TPG + 0.5 TUA)"
    return [
        ParsedAusSuperRow(
            trade_date=merger_date,
            ticker="TPM",
            side="sell",
            shares=tpm_shares,
            price=_TPG_MERGER_COST,
            notes=note,
        ),
        ParsedAusSuperRow(
            trade_date=merger_date,
            ticker="TPG",
            side="buy",
            shares=tpm_shares,
            price=_TPG_MERGER_COST,
            notes=note,
        ),
        ParsedAusSuperRow(
            trade_date=merger_date,
            ticker="TUA",
            side="buy",
            shares=tua_shares,
            price=_TUA_SPINOFF_COST,
            notes=note,
        ),
    ]


# Vault Minerals (VAU) 13:2 share consolidation, Nov 2025
_VAU_CONSOLIDATION_DATE = date(2025, 11, 20)
_VAU_CONSOLIDATION_RATIO = 2 / 13


def _vau_consolidation_trades(trades: list[ParsedAusSuperRow]) -> list[ParsedAusSuperRow]:
    """
    Vault Minerals 13:2 consolidation. Pre-consolidation lots are sold and re-bought
    at the adjusted share count and price so cost basis is unchanged.
    """
    consol_date = _VAU_CONSOLIDATION_DATE
    for tr in trades:
        if (
            tr.trade_date == consol_date
            and tr.ticker == "VAU"
            and tr.side == "sell"
            and "consolidation" in (tr.notes or "").lower()
        ):
            return []

    old_shares = _net_shares_on_or_before(trades, "VAU", consol_date)
    if old_shares <= 1e-6:
        return []

    new_shares = int(old_shares * _VAU_CONSOLIDATION_RATIO)
    if new_shares <= 0:
        return []

    avg_cost = _avg_cost_on_or_before(trades, "VAU", consol_date)
    if avg_cost <= 0:
        for tr in sorted(trades, key=lambda t: t.trade_date, reverse=True):
            if tr.ticker == "VAU" and tr.side == "buy" and tr.trade_date <= consol_date:
                avg_cost = tr.price
                break
    if avg_cost <= 0:
        avg_cost = 0.01

    new_price = avg_cost / _VAU_CONSOLIDATION_RATIO
    note = "VAU 13:2 share consolidation (Vault Minerals, Nov 2025)"
    return [
        ParsedAusSuperRow(
            trade_date=consol_date,
            ticker="VAU",
            side="sell",
            shares=old_shares,
            price=avg_cost,
            notes=note,
        ),
        ParsedAusSuperRow(
            trade_date=consol_date,
            ticker="VAU",
            side="buy",
            shares=float(new_shares),
            price=new_price,
            notes=note,
        ),
    ]


# Woodside Petroleum (WPL) → Woodside Energy (WDS) ASX ticker change, Jun 2022
_WPL_WDS_RENAME_DATE = date(2022, 6, 2)


def _first_wds_sell(trades: Sequence[ParsedAusSuperRow]) -> ParsedAusSuperRow | None:
    sells = [t for t in trades if t.ticker == "WDS" and t.side == "sell"]
    if not sells:
        return None
    return min(sells, key=lambda t: t.trade_date)


def _wpl_to_wds_rename_trades(trades: list[ParsedAusSuperRow]) -> list[ParsedAusSuperRow]:
    """
    WPL renamed to WDS (1:1). Dividend reinvestments are cash-only in alltx; the first
    WDS sell quantity reflects the full holding including reinvested shares.
    """
    rename_date = _WPL_WDS_RENAME_DATE
    for tr in trades:
        if (
            tr.trade_date == rename_date
            and tr.ticker == "WPL"
            and tr.side == "sell"
            and "rename" in (tr.notes or "").lower()
        ):
            return []

    wpl_net = _net_shares_on_or_before(trades, "WPL", rename_date)
    if wpl_net <= 1e-6:
        return []

    price = 32.6
    for tr in sorted(trades, key=lambda t: t.trade_date, reverse=True):
        if tr.ticker == "WPL" and tr.side == "buy" and tr.trade_date <= rename_date:
            price = tr.price
            break

    first_sell = _first_wds_sell(trades)
    wds_shares = first_sell.shares if first_sell is not None else wpl_net
    if wds_shares < wpl_net:
        wds_shares = wpl_net

    extra = wds_shares - wpl_net
    note = "WPL→WDS rename (1:1, ASX ticker change Jun 2022)"
    if extra > 1e-6:
        note = (
            f"{note}; includes ~{extra:,.0f} sh from dividend reinvestment "
            f"(per first WDS disposal {wds_shares:,.0f} sh)"
        )

    return [
        ParsedAusSuperRow(
            trade_date=rename_date,
            ticker="WPL",
            side="sell",
            shares=wpl_net,
            price=price,
            notes=note,
        ),
        ParsedAusSuperRow(
            trade_date=rename_date,
            ticker="WDS",
            side="buy",
            shares=wds_shares,
            price=price,
            notes=note,
        ),
    ]


def _append_parsed_trades(
    trades: list[ParsedAusSuperRow],
    additions: list[ParsedAusSuperRow],
    trade_seen: set[tuple[date, str, str, float, float]],
) -> None:
    for tr in additions:
        key = (
            tr.trade_date,
            tr.ticker,
            tr.side,
            round(tr.shares, 6),
            round(tr.price, 6),
        )
        if key in trade_seen:
            continue
        trade_seen.add(key)
        trades.append(tr)


def _month_end_balances_from_alltx(
    rows: list[dict[str, str]],
) -> dict[date, float]:
    last_in_month: dict[tuple[int, int], tuple[date, float]] = {}
    for row in rows:
        try:
            d = _parse_au_date(_row_get(row, "Date"))
        except ValueError:
            continue
        balance = _float_cell(_row_get(row, "Balance"))
        if balance <= 0:
            continue
        key = (d.year, d.month)
        prev = last_in_month.get(key)
        if prev is None or d >= prev[0]:
            last_in_month[key] = (d, balance)
    return {d: bal for d, bal in last_in_month.values()}


def load_aussuper_alltx(path: Path) -> tuple[
    list[ParsedAusSuperRow],
    list[ParsedAusSuperCashEvent],
    dict[date, float],
    float,
]:
    path = path.expanduser()
    raw_rows: list[dict[str, str]] = []
    trades: list[ParsedAusSuperRow] = []
    cash_events: list[ParsedAusSuperCashEvent] = []
    trade_seen: set[tuple[date, str, str, float, float]] = set()
    cash_seen: set[tuple[date, float, str]] = set()

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not _is_alltx_export(reader.fieldnames):
            raise ValueError(f"Not an Australian Super alltx export: {path}")
        for row in reader:
            raw_rows.append(row)
            trade = _parse_alltx_trade(row)
            if trade is not None:
                key = (
                    trade.trade_date,
                    trade.ticker,
                    trade.side,
                    round(trade.shares, 6),
                    round(trade.price, 6),
                )
                if key not in trade_seen:
                    trade_seen.add(key)
                    trades.append(trade)
                continue
            cash = _parse_alltx_cash_event(row)
            if cash is not None:
                key = (cash.event_date, round(cash.amount, 2), cash.notes)
                if key not in cash_seen:
                    cash_seen.add(key)
                    cash_events.append(cash)

    _append_parsed_trades(trades, _liquidation_sells_from_alltx(raw_rows, trades), trade_seen)
    _append_parsed_trades(trades, _takeover_sells_from_alltx(raw_rows, trades), trade_seen)
    _append_parsed_trades(trades, _tpm_merger_trades(trades), trade_seen)
    _append_parsed_trades(trades, _wpl_to_wds_rename_trades(trades), trade_seen)
    _append_parsed_trades(trades, _vau_consolidation_trades(trades), trade_seen)

    trades.sort(key=lambda r: (r.trade_date, r.ticker, r.side))
    cash_events.sort(key=lambda r: (r.event_date, r.notes))
    snapshots = _month_end_balances_from_alltx(raw_rows)
    opening = _opening_cash_from_alltx_rows(raw_rows)
    return trades, cash_events, snapshots, opening


def _parse_aussuper_row(row: dict[str, str]) -> ParsedAusSuperRow | None:
    inv_type = _row_get(row, "Investment type").strip()
    if inv_type and "Shares" not in inv_type:
        return None

    ticker = _row_get(row, "Investment").strip().upper()
    side_raw = _row_get(row, "Type").strip().lower()
    if side_raw not in ("buy", "sell"):
        return None
    if not ticker or ticker in ("--", "CASH TRANSFER"):
        return None

    filled = _float_cell(_row_get(row, "Filled"))
    if filled <= 0:
        return None

    price = _float_cell(_row_get(row, "Order price ($)", "Order price"))
    if price <= 0:
        units = _float_cell(_row_get(row, "Units"))
        value = _float_cell(_row_get(row, "Value ($)", "Value"))
        if units > 0 and value > 0:
            price = value / units
    if price <= 0:
        return None

    trade_date = _parse_au_date(_row_get(row, "Date"))
    mkt = _row_get(row, "Mkt/limit").strip()
    complete = _pct_complete(_row_get(row, "Complete (%)", "Complete"))
    notes = f"{side_raw.upper()} {ticker}"
    if mkt:
        notes += f" ({mkt})"
    if complete is not None and complete < 100:
        notes += f" — {complete:.2f}% filled"

    return ParsedAusSuperRow(
        trade_date=trade_date,
        ticker=ticker,
        side=side_raw,
        shares=filled,
        price=price,
        notes=notes,
    )


def _opening_cash_from_rows(rows: list[dict[str, str]]) -> float:
    for row in rows:
        if _row_get(row, "Type").strip().lower() != "deposit":
            continue
        inv = _row_get(row, "Investment").lower()
        inv_type = _row_get(row, "Investment type").lower()
        if "cash transfer" not in inv and "transaction account" not in inv_type:
            continue
        val = _float_cell(_row_get(row, "Value ($)", "Value"))
        if val > 0:
            return val
    return 0.0


def load_aussuper_rows(path: Path) -> tuple[list[ParsedAusSuperRow], float]:
    path = path.expanduser()
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        if _is_alltx_export(reader.fieldnames):
            trades, _, _, opening = load_aussuper_alltx(path)
            return trades, opening

    raw_rows: list[dict[str, str]] = []
    parsed: list[ParsedAusSuperRow] = []
    seen: set[tuple[date, str, str, float, float]] = set()

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_rows.append(row)
            p = _parse_aussuper_row(row)
            if p is None:
                continue
            key = (p.trade_date, p.ticker, p.side, round(p.shares, 6), round(p.price, 6))
            if key in seen:
                continue
            seen.add(key)
            parsed.append(p)

    parsed.sort(key=lambda r: (r.trade_date, r.ticker, r.side))
    return parsed, _opening_cash_from_rows(raw_rows)


def _ensure_aussuper_fund(
    conn: sqlite3.Connection,
    path: Path,
    *,
    fund_name: str | None,
    owner: str | None,
    currency: str,
    opening_cash: float,
):
    fname, fowner = resolve_aussuper_profile(path, fund_name=fund_name, owner=owner)
    fund = get_fund_by_name(conn, fname)
    if fund is None:
        fund = create_fund(conn, fname, fowner, opening_cash, currency)
    else:
        if fund.owner != fowner:
            conn.execute("UPDATE trade_funds SET owner = ? WHERE id = ?", (fowner, fund.id))
        if opening_cash > 0:
            conn.execute(
                "UPDATE trade_funds SET opening_cash = ? WHERE id = ?",
                (opening_cash, fund.id),
            )
        conn.commit()
        fund = get_fund_by_name(conn, fname)
        assert fund is not None
    return fund


def import_aussuper_alltx(
    conn: sqlite3.Connection,
    path: Path,
    *,
    fund_name: str | None = None,
    owner: str | None = None,
    currency: str = DEFAULT_CURRENCY,
    replace_existing: bool = True,
) -> AusSuperImportResult:
    """Import Australian Super all-transactions export (trades, cash events, balances)."""
    init_trade_schema(conn)
    trades, cash_events, snapshots, opening_cash = load_aussuper_alltx(path)
    fund = _ensure_aussuper_fund(
        conn,
        path,
        fund_name=fund_name,
        owner=owner,
        currency=currency,
        opening_cash=opening_cash,
    )

    if replace_existing:
        conn.execute("DELETE FROM trades WHERE fund_id = ?", (fund.id,))
        delete_fund_cash_events(conn, fund.id)
        delete_fund_performance_snapshots(conn, fund.id)

    for r in trades:
        insert_trade(
            conn,
            fund.id,
            r.trade_date,
            r.ticker,
            r.side,
            r.shares,
            r.price,
            0.0,
            r.notes,
        )
    for evt in cash_events:
        insert_fund_cash_event(conn, fund.id, evt.event_date, evt.amount, evt.notes)

    from reports.fund_balance_history import rebuild_aussuper_nav_snapshots

    statement_navs: dict[date, float] = {}
    try:
        from importers.import_aussuper_statements import (
            collect_aussuper_statement_paths,
            parse_aussuper_statement_pdf,
        )

        for pdf_path in collect_aussuper_statement_paths([path.parent]):
            snap = parse_aussuper_statement_pdf(pdf_path, fund_name=fund.name, owner=fund.owner)
            if snap is not None:
                statement_navs[snap.snapshot_date] = snap.nav
    except ImportError:
        pass

    snapshot_count = rebuild_aussuper_nav_snapshots(
        conn,
        fund,
        snapshots,
        statement_navs=statement_navs,
        replace=True,
    )

    return AusSuperImportResult(
        trades=len(trades),
        cash_events=len(cash_events),
        snapshots=snapshot_count,
        opening_cash=opening_cash,
    )


def import_aussuper_csv(
    conn: sqlite3.Connection,
    path: Path,
    *,
    fund_name: str | None = None,
    owner: str | None = None,
    currency: str = DEFAULT_CURRENCY,
    replace_existing: bool = False,
) -> AusSuperImportResult:
    """Import Australian Super CSV (order export or alltx full history)."""
    init_trade_schema(conn)
    path = path.expanduser()
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames and _is_alltx_export(reader.fieldnames):
            return import_aussuper_alltx(
                conn,
                path,
                fund_name=fund_name,
                owner=owner,
                currency=currency,
                replace_existing=replace_existing or True,
            )

    rows, opening_cash = load_aussuper_rows(path)
    fund = _ensure_aussuper_fund(
        conn,
        path,
        fund_name=fund_name,
        owner=owner,
        currency=currency,
        opening_cash=opening_cash,
    )

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
            0.0,
            r.notes,
        )

    return AusSuperImportResult(
        trades=len(rows),
        cash_events=0,
        snapshots=0,
        opening_cash=opening_cash,
    )
