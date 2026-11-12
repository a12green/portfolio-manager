from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ledger.trades_db import FundCashEvent, TradeFund, TradeRecord


@dataclass
class ProcessedTrade:
    trade: TradeRecord
    gross: float
    cash_impact: float
    cash_after: float
    realized_pl: float | None
    trade_roi_pct: float | None
    avg_cost_after: float | None
    shares_held_after: float


@dataclass
class PositionSnapshot:
    ticker: str
    shares: float
    avg_cost: float
    cost_basis: float
    mark_price: float | None
    market_value: float | None
    unrealized_pl: float | None
    unrealized_roi_pct: float | None
    pct_of_nav: float | None
    cumulative_realized_pl: float
    instrument_type: str = "stock"
    underlying: str | None = None
    expiry: date | None = None
    strike: float | None = None
    put_call: str | None = None
    contract_multiplier: float = 1.0


@dataclass
class StockSummary:
    ticker: str
    shares: float
    avg_cost: float
    avg_profit_per_share: float
    cumulative_realized_pl: float
    unrealized_pl: float | None
    total_pl: float | None


@dataclass
class FundLedgerResult:
    fund: TradeFund
    as_of: date
    processed: list[ProcessedTrade]
    positions: list[PositionSnapshot]
    stock_summaries: list[StockSummary]
    cash_balance: float
    positions_value: float
    nav: float
    invested_capital: float
    cumulative_realized_pl: float
    fund_roi_pct: float | None
    total_buy_volume: float
    total_sell_volume: float


@dataclass
class _TickerState:
    shares: float = 0.0
    avg_cost: float = 0.0
    realized_pl: float = 0.0
    instrument_type: str = "stock"
    underlying: str | None = None
    expiry: date | None = None
    strike: float | None = None
    put_call: str | None = None
    contract_multiplier: float = 1.0


def _contract_multiplier(tr: TradeRecord) -> float:
    if tr.is_option:
        return float(tr.contract_multiplier or 100.0)
    return 1.0


def _trade_gross(tr: TradeRecord, quantity: float | None = None) -> float:
    qty = tr.shares if quantity is None else quantity
    return qty * tr.price * _contract_multiplier(tr)


def _apply_trade_metadata(st: _TickerState, tr: TradeRecord) -> None:
    if st.shares > 0 and st.instrument_type != "stock":
        return
    st.instrument_type = tr.instrument_type
    st.underlying = tr.underlying
    st.expiry = tr.expiry
    st.strike = tr.strike
    st.put_call = tr.put_call
    st.contract_multiplier = _contract_multiplier(tr)


def _is_in_kind_transfer(notes: str | None) -> bool:
    """Fidelity account-to-account share moves — no cash changes hands."""
    n = (notes or "").upper()
    return "TRANSFERRED FROM" in n or "TRANSFERRED TO" in n


def _is_cash_market_sale(notes: str | None) -> bool:
    """Broker cash sale (not an in-kind transfer)."""
    n = (notes or "").upper()
    if _is_in_kind_transfer(notes):
        return False
    if "YOU SOLD" in n:
        return True
    # Hargreaves Lansdown / compact export notes (e.g. "SELL Apple").
    if n.startswith("SELL ") or "|SELL " in f"|{n}":
        return True
    return False


def _process_trades(
    fund: TradeFund,
    trades: list[TradeRecord],
    mark_prices: dict[str, float],
    as_of: date,
    *,
    cash_events: list[FundCashEvent] | None = None,
    reported_cash: float | None = None,
    strict: bool = True,
) -> FundLedgerResult:
    from ledger.quotes import lookup_mark_price

    cash = float(fund.opening_cash)
    tickers: dict[str, _TickerState] = {}
    processed: list[ProcessedTrade] = []
    total_buy = 0.0
    total_sell = 0.0

    events_by_date: list[tuple[date, FundCashEvent]] = sorted(
        ((evt.event_date, evt) for evt in (cash_events or []) if evt.event_date <= as_of),
        key=lambda x: (x[0], x[1].id),
    )
    event_idx = 0

    def _apply_due_cash_events(up_to: date) -> None:
        nonlocal cash, event_idx
        while event_idx < len(events_by_date) and events_by_date[event_idx][0] <= up_to:
            cash += events_by_date[event_idx][1].amount
            event_idx += 1

    sorted_trades = sorted(
        (tr for tr in trades if tr.trade_date <= as_of),
        key=lambda tr: (tr.trade_date, tr.id),
    )

    for tr in sorted_trades:
        _apply_due_cash_events(tr.trade_date)
        t = tr.ticker.upper()
        st = tickers.setdefault(t, _TickerState())
        _apply_trade_metadata(st, tr)
        gross = _trade_gross(tr)
        mult = _contract_multiplier(tr)
        realized: float | None = None
        roi: float | None = None

        if tr.side == "buy":
            in_kind = _is_in_kind_transfer(tr.notes)
            if not in_kind:
                total_buy += gross + tr.fees
            cash_impact = 0.0 if in_kind else -(gross + tr.fees)
            new_shares = st.shares + tr.shares
            if new_shares > 0:
                st.avg_cost = (
                    (st.shares * st.avg_cost + tr.shares * tr.price) / new_shares
                    if st.shares > 0
                    else tr.price
                )
            st.shares = new_shares
        else:
            in_kind = _is_in_kind_transfer(tr.notes)
            sell_shares = tr.shares
            if sell_shares > st.shares + 1e-9:
                if strict:
                    raise ValueError(
                        f"Sell {tr.shares} {t} on {tr.trade_date} exceeds held {st.shares:.4f}"
                    )
                sell_shares = st.shares
            if sell_shares <= 1e-9:
                if in_kind:
                    continue
                if _is_cash_market_sale(tr.notes):
                    gross = _trade_gross(tr)
                    total_sell += gross - tr.fees
                    cash_impact = gross - tr.fees
                    cash += cash_impact
                    processed.append(
                        ProcessedTrade(
                            trade=tr,
                            gross=gross,
                            cash_impact=cash_impact,
                            cash_after=cash,
                            realized_pl=None,
                            trade_roi_pct=None,
                            avg_cost_after=st.avg_cost if st.shares > 0 else None,
                            shares_held_after=st.shares,
                        )
                    )
                continue
            gross = _trade_gross(tr, quantity=sell_shares)
            if not in_kind:
                total_sell += gross - tr.fees
            cost_basis_sold = st.avg_cost * sell_shares * mult
            realized = gross - tr.fees - cost_basis_sold
            roi = (realized / cost_basis_sold * 100.0) if cost_basis_sold else None
            st.realized_pl += realized
            st.shares -= sell_shares
            if st.shares <= 1e-9:
                st.shares = 0.0
                st.avg_cost = 0.0
            cash_impact = 0.0 if in_kind else gross - tr.fees

        cash += cash_impact
        processed.append(
            ProcessedTrade(
                trade=tr,
                gross=gross,
                cash_impact=cash_impact,
                cash_after=cash,
                realized_pl=realized,
                trade_roi_pct=roi,
                avg_cost_after=st.avg_cost if st.shares > 0 else None,
                shares_held_after=st.shares,
            )
        )

    if sorted_trades:
        _apply_due_cash_events(sorted_trades[-1].trade_date)
    else:
        _apply_due_cash_events(as_of)

    positions: list[PositionSnapshot] = []
    pos_value = 0.0
    cum_realized = sum(s.realized_pl for s in tickers.values())

    for ticker, st in sorted(tickers.items()):
        if st.shares <= 0:
            continue
        mark = lookup_mark_price(mark_prices, ticker)
        mult = st.contract_multiplier
        cost_basis = st.shares * st.avg_cost * mult
        mkt = st.shares * mark * mult if mark is not None else None
        unpl = (mkt - cost_basis) if mkt is not None else None
        unroi = (unpl / cost_basis * 100.0) if unpl is not None and cost_basis else None
        if mkt is not None:
            pos_value += mkt
        positions.append(
            PositionSnapshot(
                ticker=ticker,
                shares=st.shares,
                avg_cost=st.avg_cost,
                cost_basis=cost_basis,
                mark_price=mark,
                market_value=mkt,
                unrealized_pl=unpl,
                unrealized_roi_pct=unroi,
                pct_of_nav=None,
                cumulative_realized_pl=st.realized_pl,
                instrument_type=st.instrument_type,
                underlying=st.underlying,
                expiry=st.expiry,
                strike=st.strike,
                put_call=st.put_call,
                contract_multiplier=mult,
            )
        )

    if reported_cash is not None:
        cash = float(reported_cash)

    nav = cash + pos_value
    for p in positions:
        p.pct_of_nav = (p.market_value / nav * 100.0) if p.market_value is not None and nav else None

    stock_summaries: list[StockSummary] = []
    for ticker, st in sorted(tickers.items()):
        if st.shares <= 0 and st.realized_pl == 0:
            continue
        mark = lookup_mark_price(mark_prices, ticker)
        mult = st.contract_multiplier
        unpl = None
        if st.shares > 0 and mark is not None:
            unpl = st.shares * mark * mult - st.shares * st.avg_cost * mult
        if st.shares > 0:
            avg_profit_per_share = (st.realized_pl + (unpl or 0.0)) / st.shares
        else:
            avg_profit_per_share = 0.0
        total_pl = st.realized_pl + (unpl or 0.0) if (st.realized_pl or unpl is not None) else None
        stock_summaries.append(
            StockSummary(
                ticker=ticker,
                shares=st.shares,
                avg_cost=st.avg_cost,
                avg_profit_per_share=avg_profit_per_share,
                cumulative_realized_pl=st.realized_pl,
                unrealized_pl=unpl,
                total_pl=total_pl,
            )
        )

    invested = fund.opening_cash + total_buy - total_sell
    fund_roi = ((nav - invested) / invested * 100.0) if invested else None

    return FundLedgerResult(
        fund=fund,
        as_of=as_of,
        processed=processed,
        positions=positions,
        stock_summaries=stock_summaries,
        cash_balance=cash,
        positions_value=pos_value,
        nav=nav,
        invested_capital=invested,
        cumulative_realized_pl=cum_realized,
        fund_roi_pct=fund_roi,
        total_buy_volume=total_buy,
        total_sell_volume=total_sell,
    )


def build_fund_ledger(
    fund: TradeFund,
    trades: list[TradeRecord],
    mark_prices: dict[str, float] | None = None,
    as_of: date | None = None,
    *,
    cash_events: list[FundCashEvent] | None = None,
    reported_cash: float | None = None,
    strict: bool = True,
) -> FundLedgerResult:
    as_of = as_of or date.today()
    return _process_trades(
        fund,
        trades,
        mark_prices or {},
        as_of,
        cash_events=cash_events,
        reported_cash=reported_cash,
        strict=strict,
    )


def build_fund_ledger_with_db(
    conn,
    fund: TradeFund,
    mark_prices: dict[str, float] | None = None,
    as_of: date | None = None,
    *,
    strict: bool = True,
) -> FundLedgerResult:
    """Build ledger using trades, cash events, and broker-reported cash from SQLite."""
    from ledger.trades_db import (
        fetch_fund_cash_events,
        fetch_fund_reported_cash,
        fetch_trades_for_fund,
    )

    as_of = as_of or date.today()
    trades = fetch_trades_for_fund(conn, fund.id)
    cash_events = fetch_fund_cash_events(conn, fund.id)
    reported = fetch_fund_reported_cash(conn, fund.id)
    reported_cash = reported.cash_balance if reported else None
    return build_fund_ledger(
        fund,
        trades,
        mark_prices,
        as_of,
        cash_events=cash_events,
        reported_cash=reported_cash,
        strict=strict,
    )
