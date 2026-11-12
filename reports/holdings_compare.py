from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
from dataclasses import dataclass
from datetime import date

from ledger.trade_ledger import build_fund_ledger, build_fund_ledger_with_db
from ledger.trades_db import (
    fetch_trades_for_fund,
    init_trade_schema,
    list_funds,
)
from ledger.quotes import marks_for_fund

_ALL_OWNERS = (OWNER_A, OWNER_B, OWNER_C)


def _owner_is_all(owner: str) -> bool:
    return owner.strip().lower() == "all"


def _funds_in_scope(conn, owner: str, fund_name: str | None = None):
    init_trade_schema(conn)
    if _owner_is_all(owner):
        funds = list_funds(conn)
    else:
        funds = [f for f in list_funds(conn) if f.owner == owner.strip()]
    if fund_name and fund_name.strip().lower() != "all":
        funds = [f for f in funds if f.name == fund_name.strip()]
    return funds


def collect_ledger_shares(
    conn,
    *,
    owner: str,
    fund_name: str | None = None,
    as_of: date | None = None,
) -> dict[str, float]:
    """Open-position share counts from trade ledgers (source of truth)."""
    as_of = as_of or date.today()
    funds = _funds_in_scope(conn, owner, fund_name)
    out: dict[str, float] = {}
    for fund in funds:
        marks = marks_for_fund(conn, fund, as_of, live=False)
        trades = fetch_trades_for_fund(conn, fund.id)
        if not trades:
            continue
        result = build_fund_ledger(fund, trades, marks, as_of, strict=False)
        for p in result.positions:
            if p.shares <= 0:
                continue
            ticker = p.ticker.upper()
            out[ticker] = out.get(ticker, 0.0) + p.shares
    return out


def _position_market(
    ticker: str,
    fund_currency: str,
    *,
    instrument_type: str = "stock",
    underlying: str | None = None,
) -> str:
    """AU for ASX / AUD-fund listings; otherwise US."""
    if instrument_type == "option":
        return _position_market(underlying or ticker, fund_currency)
    tick = str(ticker).strip().upper()
    if tick.startswith("ASX:"):
        return "AU"
    cur = (fund_currency or "USD").strip().upper()
    if cur == "AUD":
        return "AU"
    from ledger.quotes import infer_mark_currency

    if infer_mark_currency(ticker) == "AUD":
        return "AU"
    return "US"


@dataclass(frozen=True)
class LedgerCardPosition:
    ticker: str
    shares: float
    avg_cost: float
    mark_price: float | None
    market_value: float | None
    unrealized_pl: float | None
    pl_pct: float
    currency: str
    market: str = "US"
    instrument_type: str = "stock"
    underlying: str | None = None
    expiry: date | None = None
    strike: float | None = None
    put_call: str | None = None
    contract_multiplier: float = 1.0
    display_name: str | None = None


def _marks_for_fund_from_owner_marks(
    conn,
    fund,
    owner_marks: dict[str, tuple[float, str]],
    as_of: date,
    *,
    fx_rates: dict[str, float],
) -> dict[str, float]:
    """Build per-fund marks without re-fetching owner quotes or FX."""
    from bisect import bisect_right

    from ledger.quotes import normalize_ticker, price_in_fund_currency
    from ledger.trades_db import fetch_fund_ticker_marks

    fund_cur = (fund.currency or "USD").strip().upper()
    out = {
        normalize_ticker(ticker): price_in_fund_currency(price, mark_cur, fund_cur, fx_rates)
        for ticker, (price, mark_cur) in owner_marks.items()
    }
    fund_marks = fetch_fund_ticker_marks(conn, fund.id)
    if fund_marks:
        for ticker, (dates, prices) in fund_marks.items():
            if not dates:
                continue
            idx = bisect_right(dates, as_of) - 1
            if idx < 0 or prices[idx] <= 0:
                continue
            tk = ticker.upper()
            # See matching comment in quotes.py::marks_for_fund -- only let a
            # stale fund-specific mark override a live owner quote when it's
            # within a statement-cadence window of as_of; otherwise fall back
            # to it only if there's no live quote at all.
            fresh = (as_of - dates[idx]).days <= 45
            if fresh or tk not in out:
                out[tk] = prices[idx]
    return out


@dataclass(frozen=True)
class FundHoldingRow:
    """All price/value fields are USD, regardless of the fund's own reporting
    currency (native_currency), so rows are directly comparable across funds."""

    owner: str
    fund: str
    ticker: str
    shares: float
    avg_cost: float
    mark_price: float | None
    market_value: float | None
    unrealized_pl: float | None
    pct_of_fund: float | None
    native_currency: str
    market: str = "US"
    instrument_type: str = "stock"
    underlying: str | None = None


def collect_fund_holdings_rows(
    conn,
    *,
    owner: str,
    as_of: date | None = None,
    live: bool = False,
    force_live: bool = False,
) -> list[FundHoldingRow]:
    """Open positions from trade ledgers, one row per (fund, ticker) — not
    merged across funds, so the same ticker held in multiple funds shows up
    as separate rows for direct fund-to-fund comparison."""
    from ledger.quotes import convert_to_usd, fetch_owner_marks_with_currency, fx_to_usd_rates

    as_of = as_of or date.today()
    funds = _funds_in_scope(conn, owner)
    fx_rates = fx_to_usd_rates(allow_network=live or force_live)
    owner_marks_cache: dict[str, dict[str, tuple[float, str]]] = {}

    rows: list[FundHoldingRow] = []
    for fund in funds:
        owner_key = fund.owner.strip()
        if owner_key not in owner_marks_cache:
            owner_marks_cache[owner_key] = fetch_owner_marks_with_currency(
                conn, owner_key, live=live, force_live=force_live
            )
        marks = _marks_for_fund_from_owner_marks(
            conn, fund, owner_marks_cache[owner_key], as_of, fx_rates=fx_rates
        )
        result = build_fund_ledger_with_db(conn, fund, marks, as_of, strict=False)
        fund_cur = (fund.currency or "USD").strip().upper()
        for p in result.positions:
            if p.shares <= 0:
                continue
            ticker = p.ticker.upper()

            def _usd(v: float | None) -> float | None:
                return convert_to_usd(float(v), fund_cur, fx_rates) if v is not None else None

            pos_market = _position_market(
                ticker,
                fund_cur,
                instrument_type=p.instrument_type,
                underlying=p.underlying,
            )
            rows.append(
                FundHoldingRow(
                    owner=fund.owner,
                    fund=fund.name,
                    ticker=ticker,
                    shares=p.shares,
                    avg_cost=_usd(p.avg_cost) or 0.0,
                    mark_price=_usd(p.mark_price),
                    market_value=_usd(p.market_value),
                    unrealized_pl=_usd(p.unrealized_pl),
                    pct_of_fund=p.pct_of_nav,
                    native_currency=fund_cur,
                    market=pos_market,
                    instrument_type=p.instrument_type,
                    underlying=p.underlying,
                )
            )
    return rows


def collect_ledger_card_positions(
    conn,
    *,
    owner: str,
    fund_name: str | None = None,
    as_of: date | None = None,
    live: bool = False,
    force_live: bool = False,
) -> list[LedgerCardPosition]:
    """Open positions from trade ledgers for portfolio cards."""
    from ledger.quotes import fetch_owner_marks_with_currency, fx_to_usd_rates

    as_of = as_of or date.today()
    funds = _funds_in_scope(conn, owner, fund_name)
    fx_rates = fx_to_usd_rates(allow_network=live or force_live)
    owner_marks_cache: dict[str, dict[str, tuple[float, str]]] = {}

    from ledger.quotes import convert_to_usd

    merge_across_funds = len(funds) > 1
    merged: dict[str, dict[str, float]] = {}
    for fund in funds:
        owner_key = fund.owner.strip()
        if owner_key not in owner_marks_cache:
            owner_marks_cache[owner_key] = fetch_owner_marks_with_currency(
                conn, owner_key, live=live, force_live=force_live
            )
        marks = _marks_for_fund_from_owner_marks(
            conn, fund, owner_marks_cache[owner_key], as_of, fx_rates=fx_rates
        )
        result = build_fund_ledger_with_db(conn, fund, marks, as_of, strict=False)
        fund_cur = (fund.currency or "USD").strip().upper()
        for p in result.positions:
            if p.shares <= 0:
                continue
            ticker = p.ticker.upper()
            mkt = float(p.market_value or 0.0)
            cost = float(p.cost_basis or 0.0)
            if merge_across_funds:
                mkt = convert_to_usd(mkt, fund_cur, fx_rates)
                cost = convert_to_usd(cost, fund_cur, fx_rates)
            pos_market = _position_market(
                ticker,
                fund_cur,
                instrument_type=p.instrument_type,
                underlying=p.underlying,
            )
            bucket = merged.setdefault(
                ticker,
                {
                    "shares": 0.0,
                    "cost": 0.0,
                    "mkt": 0.0,
                    "realized": 0.0,
                    "currency": "USD" if merge_across_funds else fund_cur,
                    "market": pos_market,
                    "instrument_type": p.instrument_type,
                    "underlying": p.underlying,
                    "expiry": p.expiry,
                    "strike": p.strike,
                    "put_call": p.put_call,
                    "contract_multiplier": p.contract_multiplier,
                    "premium_cost": 0.0,
                    "premium_shares": 0.0,
                },
            )
            if pos_market == "AU":
                bucket["market"] = "AU"
            bucket["shares"] += p.shares
            bucket["cost"] += cost
            bucket["mkt"] += mkt
            bucket["realized"] += float(p.cumulative_realized_pl or 0.0)
            bucket["premium_cost"] += p.shares * p.avg_cost
            bucket["premium_shares"] += p.shares

    from ledger.option_symbols import display_option_label

    out: list[LedgerCardPosition] = []
    for ticker, vals in merged.items():
        shares = vals["shares"]
        cost = vals["cost"]
        mkt = vals["mkt"]
        mult = float(vals.get("contract_multiplier") or 1.0)
        is_option = vals.get("instrument_type") == "option"
        if is_option and shares > 0:
            avg = vals["premium_cost"] / vals["premium_shares"] if vals["premium_shares"] else 0.0
            mark = mkt / (shares * mult) if mkt > 0 and mult > 0 else None
        else:
            avg = cost / shares if shares > 0 else 0.0
            mark = mkt / shares if shares > 0 else None
        unreal = mkt - cost if mkt > 0 else None
        pl_pct = (unreal / cost * 100.0) if unreal is not None and cost > 0 else 0.0
        display = None
        if is_option:
            display = display_option_label(
                underlying=vals.get("underlying"),
                expiry=vals.get("expiry"),
                strike=vals.get("strike"),
                put_call=vals.get("put_call"),
                occ_symbol=ticker,
            )
        out.append(
            LedgerCardPosition(
                ticker=ticker,
                shares=shares,
                avg_cost=avg,
                mark_price=mark,
                market_value=mkt if mkt > 0 else None,
                unrealized_pl=unreal,
                pl_pct=pl_pct,
                currency=str(vals["currency"] or "USD"),
                market=str(vals.get("market") or "US"),
                instrument_type=str(vals.get("instrument_type") or "stock"),
                underlying=vals.get("underlying"),
                expiry=vals.get("expiry"),
                strike=vals.get("strike"),
                put_call=vals.get("put_call"),
                contract_multiplier=mult,
                display_name=display,
            )
        )
    out.sort(key=lambda p: -(p.market_value or 0.0))
    return out
