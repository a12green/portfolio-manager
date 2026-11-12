"""Business operations shared by the web UI and the desktop GUI."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.models import BalanceRow, BalanceView, FundSummary, HeatmapView, LedgerDetail, LedgerPosition
from ledger.db import connect_db
from ledger.option_symbols import display_option_label
from ledger.trade_ledger import build_fund_ledger_with_db
from ledger.trades_db import get_fund_by_name, init_trade_schema, list_funds
from ledger.quotes import marks_for_fund
from reports.holdings_compare import collect_ledger_card_positions


def _is_all(value: str | None) -> bool:
    return not value or value.strip().lower() == "all"


def ledger_detail(
    db_path: Path,
    *,
    owner: str,
    fund: str | None,
    live: bool,
    as_of: date | None = None,
) -> LedgerDetail:
    """Positions and one-line summaries for an owner, or for a single fund when one is named.

    Zero-share positions are left out of the fund view. The owner view lists every position.
    """
    as_of = as_of or date.today()
    fund_filter = None if _is_all(fund) else fund.strip()
    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        if fund_filter:
            return _fund_ledger(conn, fund_filter, live=live, as_of=as_of)
        return _owner_ledger(conn, owner, live=live, as_of=as_of)
    finally:
        conn.close()


def _fund_ledger(conn, fund_name: str, *, live: bool, as_of: date) -> LedgerDetail:
    fund_row = get_fund_by_name(conn, fund_name)
    if fund_row is None:
        return LedgerDetail(fund_filter=fund_name)
    marks = marks_for_fund(conn, fund_row, as_of, live=live, force_live=live)
    result = build_fund_ledger_with_db(conn, fund_row, marks, as_of, strict=False)
    positions = []
    for p in sorted(result.positions, key=lambda x: -(x.pct_of_nav or 0)):
        if p.shares <= 0:
            continue
        label = p.ticker
        if p.instrument_type == "option":
            label = display_option_label(
                underlying=p.underlying,
                expiry=p.expiry,
                strike=p.strike,
                put_call=p.put_call,
                occ_symbol=p.ticker,
            )
        positions.append(
            LedgerPosition(
                ticker=p.ticker,
                display_name=label,
                instrument_type=p.instrument_type,
                shares=p.shares,
                avg_cost=p.avg_cost,
                mark_price=p.mark_price,
                market_value=p.market_value,
                unrealized_pl=p.unrealized_pl,
                realized_pl=p.cumulative_realized_pl,
                currency=fund_row.currency,
            )
        )
    summary = FundSummary(
        name=fund_row.name,
        currency=fund_row.currency,
        cash=result.cash_balance,
        nav=result.nav,
        roi_pct=result.fund_roi_pct,
    )
    return LedgerDetail(positions=positions, funds=[summary], fund_filter=fund_name)


def _owner_ledger(conn, owner: str, *, live: bool, as_of: date) -> LedgerDetail:
    positions = [
        LedgerPosition(
            ticker=pos.ticker,
            display_name=pos.display_name or pos.ticker,
            instrument_type=pos.instrument_type,
            shares=pos.shares,
            avg_cost=pos.avg_cost,
            mark_price=pos.mark_price,
            market_value=pos.market_value,
            unrealized_pl=pos.unrealized_pl,
            realized_pl=None,
            currency=pos.currency,
        )
        for pos in collect_ledger_card_positions(conn, owner=owner, fund_name=None, live=live, force_live=live)
    ]
    funds = list_funds(conn) if _is_all(owner) else [f for f in list_funds(conn) if f.owner == owner]
    summaries = []
    for f in funds:
        marks = marks_for_fund(conn, f, as_of, live=live, force_live=live)
        result = build_fund_ledger_with_db(conn, f, marks, as_of, strict=False)
        summaries.append(
            FundSummary(name=f.name, currency=f.currency, cash=result.cash_balance,
                        nav=result.nav, roi_pct=result.fund_roi_pct)
        )
    return LedgerDetail(positions=positions, funds=summaries)


def heatmap(
    db_path: Path,
    *,
    owner: str | None,
    fund: str | None,
    color_by: str,
    live: bool,
) -> HeatmapView:
    """Heat-map tiles for an owner or fund, the colour mode, and the total value in USD.

    ``owner`` and ``fund`` of None (or "All") mean no filter. ``color_by`` starting with
    "day" colours by today's change; anything else colours by overall P/L.
    """

    from ledger.quotes import sum_tiles_value_usd
    from reports.heatmap_data import collect_heatmap_tiles

    owner_arg = None if _is_all(owner) else owner.strip()
    fund_arg = None if _is_all(fund) else fund.strip()
    mode = "day" if color_by.lower().startswith("day") else "overall"
    tiles = collect_heatmap_tiles(db_path, owner=owner_arg, fund_name=fund_arg, live=live, color_by=mode)
    return HeatmapView(
        tiles=list(tiles),
        color_by=mode,
        total_usd=sum_tiles_value_usd(tiles, force_fx=live),
        owner=owner_arg,
        fund=fund_arg,
    )


def market_movers(market: str, *, force: bool = False):
    """Top gainers and losers for "AU" or "US". Raises if the quote provider cannot be reached.

    Callers decide how to show a failure: the web payload returns an empty list with an
    "unavailable" flag, and the GUI shows a message box.
    """
    from ledger.market_movers import fetch_market_movers

    mkt = "AU" if market.upper().startswith("AU") or market.lower().startswith("aust") else "US"
    gainers, losers = fetch_market_movers(mkt, force=force)  # type: ignore[arg-type]
    return mkt, list(gainers), list(losers)


def portfolio_holdings(
    db_path: Path,
    *,
    owner: str,
    fund: str | None,
    market: str,
    live: bool,
    owner_label: str,
):
    """Holdings cards and the one-line summary for the Current portfolio view."""
    from reports.portfolio_data import load_portfolio_holdings

    fund_filter = None if _is_all(fund) else fund.strip()
    return load_portfolio_holdings(
        db_path,
        owner=owner,
        fund_filter=fund_filter,
        market_sel=market,
        use_live=live,
        owner_label=owner_label,
    )


def net_worth(db_path: Path, *, owner: str, use_live: bool = False):
    """Owner net worth: each fund (manual balance, or ledger total) and other asset, with FX."""
    from reports.net_worth import compute_owner_net_worth

    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        return compute_owner_net_worth(conn, owner.strip(), use_live=use_live)
    finally:
        conn.close()


def fund_balance_rows(db_path: Path) -> BalanceView:
    """Every fund with its stored manual balance and its current ledger totals (local currency)."""
    from ledger.trade_ledger import build_fund_ledger_with_db
    from ledger.trades_db import fetch_fund_manual_balances

    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        funds = list_funds(conn)
        manual = fetch_fund_manual_balances(conn)
        rows = []
        for fund in funds:
            try:
                marks = marks_for_fund(conn, fund, date.today(), live=False)
                result = build_fund_ledger_with_db(conn, fund, marks, date.today(), strict=False)
                ledger_totals = (result.cash_balance, result.positions_value, result.nav)
            except Exception:
                ledger_totals = None
            rows.append(BalanceRow(fund=fund, manual=manual.get(fund.id), ledger=ledger_totals))
        return BalanceView(rows=rows, manual_count=len(manual))
    finally:
        conn.close()


def fund_trends(
    db_path: Path,
    *,
    owner: str | None,
    fund: str | None,
    location: str = "All",
    trends_mode: str = "statements",
):
    """Historical NAV series for funds. Each series keeps its own currency."""
    from reports.fund_balance_history import collect_fund_balance_series

    return collect_fund_balance_series(
        db_path,
        owner=None if _is_all(owner) else owner.strip(),
        fund_name=None if _is_all(fund) else fund.strip(),
        location=location,
        trends_mode=trends_mode,  # type: ignore[arg-type]
    )


def series_nav_usd(series, fx: dict[str, float]) -> list[float]:
    """The series' NAV in USD, converted with the given rates (1.0 when a currency is unknown)."""
    rate = fx.get(series.currency.upper(), 1.0)
    return [p.nav * rate for p in series.points]


# --- writes -------------------------------------------------------------------------
#
# Every write takes ``owner_scope``: None means an administrator (any fund), otherwise the
# operation is refused with PermissionError unless the fund belongs to that owner.


def _check_scope(owner_scope: str | None, owner: str) -> None:
    if owner_scope is not None and owner.strip() != owner_scope:
        raise PermissionError("That fund belongs to another owner.")


def _fund_for_write(conn, fund_name: str, owner_scope: str | None):
    fund = get_fund_by_name(conn, fund_name.strip())
    if fund is None:
        raise ValueError(f"Unknown fund {fund_name!r}")
    _check_scope(owner_scope, fund.owner)
    return fund


def _after_data_change() -> None:
    """Drop cached prices and charts so the next view shows the new data."""
    from ledger.quotes import clear_historical_close_cache, clear_live_quotes_cache
    from reports.fund_balance_history import clear_fund_balance_series_cache
    from reports.heatmap_data import clear_heatmap_caches

    clear_live_quotes_cache()
    clear_heatmap_caches()
    clear_fund_balance_series_cache()
    clear_historical_close_cache()


def record_trade(
    db_path: Path,
    *,
    fund_name: str,
    ticker: str,
    side: str,
    shares: float,
    price: float,
    trade_date: date | None = None,
    fees: float = 0.0,
    notes: str | None = None,
    instrument_type: str = "stock",
    underlying: str | None = None,
    expiry: date | None = None,
    strike: float | None = None,
    put_call: str | None = None,
    contract_multiplier: float | None = None,
    owner_scope: str | None = None,
):
    """Record a buy or sell in a fund and return the stored trade."""
    from ledger.trades_db import fetch_trades_for_fund, insert_trade

    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        fund = _fund_for_write(conn, fund_name, owner_scope)
        tid = insert_trade(
            conn,
            fund.id,
            trade_date or date.today(),
            ticker.strip().upper(),
            side.strip().lower(),
            shares,
            price,
            fees,
            notes,
            instrument_type=instrument_type,
            underlying=underlying,
            expiry=expiry,
            strike=strike,
            put_call=put_call,
            contract_multiplier=contract_multiplier,
        )
        trade = next(t for t in fetch_trades_for_fund(conn, fund.id) if t.id == tid)
    finally:
        conn.close()
    _after_data_change()
    return trade


def create_fund(
    db_path: Path,
    *,
    name: str,
    owner: str,
    opening_cash: float,
    currency: str,
    owner_scope: str | None = None,
):
    """Create a trade fund for one of the configured owners."""
    from config import OWNERS
    from ledger.trades_db import create_fund as _create_fund

    clean = name.strip()
    if owner.strip() not in OWNERS:
        raise ValueError(f"Unknown owner {owner!r}. Use one of: {', '.join(OWNERS)}")
    _check_scope(owner_scope, owner.strip())
    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        if get_fund_by_name(conn, clean):
            raise ValueError(f"Fund {clean!r} already exists.")
        return _create_fund(conn, clean, owner.strip(), opening_cash, currency.strip().upper())
    finally:
        conn.close()


def save_fund_balance(
    db_path: Path,
    *,
    fund_name: str,
    as_of: date,
    nav: float,
    cash: float | None = None,
    positions: float | None = None,
    notes: str | None = None,
    owner_scope: str | None = None,
) -> None:
    """Store a manual balance for a fund, which then overrides the ledger total in net worth."""
    from ledger.trades_db import upsert_fund_manual_balance

    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        fund = _fund_for_write(conn, fund_name, owner_scope)
        upsert_fund_manual_balance(
            conn, fund.id, as_of, nav, cash_balance=cash, positions_value=positions, notes=notes
        )
    finally:
        conn.close()
    _after_data_change()


def clear_fund_balance(db_path: Path, *, fund_name: str, owner_scope: str | None = None) -> bool:
    """Remove a fund's manual balance. Returns False when none was stored."""
    from ledger.trades_db import clear_fund_manual_balance

    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        fund = _fund_for_write(conn, fund_name, owner_scope)
        removed = clear_fund_manual_balance(conn, fund.id)
    finally:
        conn.close()
    _after_data_change()
    return bool(removed)


def save_other_asset(
    db_path: Path,
    *,
    owner: str,
    asset_type: str,
    amount: float,
    currency: str = "USD",
    notes: str | None = None,
    owner_scope: str | None = None,
) -> None:
    """Add or update a non-fund asset (cash held outside a fund, property, and so on)."""
    from ledger.trades_db import upsert_owner_other_asset

    _check_scope(owner_scope, owner.strip())
    conn = connect_db(db_path)
    try:
        upsert_owner_other_asset(
            conn, owner.strip(), asset_type.strip(), amount, currency=currency, notes=notes
        )
    finally:
        conn.close()


def delete_other_asset(
    db_path: Path, *, owner: str, asset_type: str, owner_scope: str | None = None
) -> bool:
    from ledger.trades_db import delete_owner_other_asset

    _check_scope(owner_scope, owner.strip())
    conn = connect_db(db_path)
    try:
        return bool(delete_owner_other_asset(conn, owner.strip(), asset_type.strip()))
    finally:
        conn.close()


def fund_trades(db_path: Path, *, fund_name: str, owner_scope: str | None = None) -> list:
    """Every trade in a fund, oldest first. Raises ValueError for an unknown fund."""
    from ledger.trades_db import fetch_trades_for_fund

    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        fund = _fund_for_write(conn, fund_name, owner_scope)
        return list(fetch_trades_for_fund(conn, fund.id))
    finally:
        conn.close()


def fund_holdings(db_path: Path, *, owner: str, live: bool):
    """Holdings listed per fund for an owner (or all owners), with cost, mark and P/L."""
    from reports.holdings_compare import collect_fund_holdings_rows

    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        return collect_fund_holdings_rows(conn, owner=owner, live=live, force_live=live)
    finally:
        conn.close()
