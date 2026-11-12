"""API helpers — thin wrappers over existing portfolio modules."""

from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import trades_cli
from ledger.db import connect_db
from reports.holdings_compare import compare_ledger_to_sheet, collect_ledger_card_positions
from reports.portfolio_cards import HoldingCardData
from reports.portfolio_data import load_portfolio_holdings
from ledger.trade_ledger import build_fund_ledger_with_db
from ledger.trades_db import (
    TradeFund,
    TradeRecord,
    create_fund,
    fetch_trades_for_fund,
    get_fund_by_name,
    init_trade_schema,
    insert_trade,
    list_funds,
)
from ledger.quotes import live_quotes_enabled, marks_for_fund

if TYPE_CHECKING:
    from web.auth import WebUser

_OWNERS = (OWNER_A, OWNER_B, OWNER_C)
_DISPLAY = {OWNER_A: OWNER_A, OWNER_C: OWNER_C, OWNER_B: OWNER_B}


def owner_label(owner: str) -> str:
    if owner.strip().lower() == "all":
        return "All owners"
    return _DISPLAY.get(owner, owner)


def _jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


def holding_to_dict(card: HoldingCardData) -> dict[str, Any]:
    d = _jsonable(card)
    price_diff = (card.last - card.avg_cost) if card.avg_cost > 0 else None
    d["price_diff"] = price_diff
    return d


def list_funds_payload(user: "WebUser | None" = None) -> list[dict[str, Any]]:
    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        funds = list_funds(conn)
        if user is not None and not user.is_admin:
            funds = [f for f in funds if f.owner == user.owner_scope]
        return [
            {
                "id": f.id,
                "name": f.name,
                "owner": f.owner,
                "currency": f.currency,
                "opening_cash": f.opening_cash,
            }
            for f in funds
        ]
    finally:
        conn.close()


def create_fund_payload(
    name: str,
    owner: str,
    opening_cash: float,
    currency: str,
    *,
    user: "WebUser | None" = None,
) -> dict[str, Any]:
    if user is not None:
        # Enforce owner scope here too, not only in the route: a caller must not
        # create a fund for an owner it cannot see.
        from web import auth

        auth.assert_owner_field(user, owner)
    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        if get_fund_by_name(conn, name):
            raise ValueError(f"Fund {name!r} already exists.")
        fund = create_fund(conn, name, owner, opening_cash, currency)
        return _fund_dict(fund)
    finally:
        conn.close()


def _fund_dict(fund: TradeFund) -> dict[str, Any]:
    return {
        "id": fund.id,
        "name": fund.name,
        "owner": fund.owner,
        "currency": fund.currency,
        "opening_cash": fund.opening_cash,
    }


def list_trades_payload(fund_name: str, *, user: "WebUser | None" = None) -> list[dict[str, Any]]:
    conn = connect_db(trades_cli._db_path())
    try:
        fund = get_fund_by_name(conn, fund_name)
        if not fund:
            raise ValueError(f"Unknown fund {fund_name!r}")
        if user is not None:
            from web.auth import assert_fund_access

            assert_fund_access(user, fund)
        # Newest first for the Record trade screen; fetch_trades_for_fund stays
        # chronological (ASC) since ledger replay elsewhere depends on that order.
        return [_trade_dict(t) for t in reversed(fetch_trades_for_fund(conn, fund.id))]
    finally:
        conn.close()


def _trade_dict(t: TradeRecord) -> dict[str, Any]:
    from ledger.option_symbols import display_option_label

    display = t.ticker
    if t.is_option:
        display = display_option_label(
            underlying=t.underlying,
            expiry=t.expiry,
            strike=t.strike,
            put_call=t.put_call,
            occ_symbol=t.ticker,
        )
    return {
        "id": t.id,
        "trade_date": t.trade_date.isoformat(),
        "ticker": t.ticker,
        "display_name": display,
        "side": t.side,
        "shares": t.shares,
        "price": t.price,
        "fees": t.fees,
        "notes": t.notes or "",
        "instrument_type": t.instrument_type,
        "underlying": t.underlying,
        "expiry": t.expiry.isoformat() if t.expiry else None,
        "strike": t.strike,
        "put_call": t.put_call,
        "contract_multiplier": t.contract_multiplier,
    }


def add_trade_payload(
    fund_name: str,
    ticker: str,
    side: str,
    shares: float,
    price: float,
    trade_date: date | None,
    fees: float,
    notes: str | None,
    *,
    user: "WebUser | None" = None,
    instrument_type: str = "stock",
    underlying: str | None = None,
    expiry: date | None = None,
    strike: float | None = None,
    put_call: str | None = None,
    contract_multiplier: float | None = None,
) -> dict[str, Any]:
    from ledger.quotes import clear_live_quotes_cache
    from reports.heatmap_data import clear_heatmap_caches

    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        fund = get_fund_by_name(conn, fund_name)
        if not fund:
            raise ValueError(f"Unknown fund {fund_name!r}")
        if user is not None:
            from web.auth import assert_fund_access

            assert_fund_access(user, fund)
        tid = insert_trade(
            conn,
            fund.id,
            trade_date or date.today(),
            ticker.strip().upper(),
            side.lower(),
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
        trades = fetch_trades_for_fund(conn, fund.id)
        trade = next(t for t in trades if t.id == tid)
    finally:
        conn.close()
    clear_live_quotes_cache()
    clear_heatmap_caches()
    return _trade_dict(trade)


def portfolio_payload(
    *,
    owner: str,
    fund: str | None,
    market: str,
    live: bool,
) -> dict[str, Any]:
    fund_filter = None if not fund or fund.lower() == "all" else fund
    cards, summary = load_portfolio_holdings(
        trades_cli._db_path(),
        owner=owner,
        fund_filter=fund_filter,
        market_sel=market,
        use_live=live,
        owner_label=owner_label(owner),
        include_sheet_compare=live,
    )
    return {
        "summary": summary,
        "holdings": [holding_to_dict(c) for c in cards],
        "live": live and live_quotes_enabled(force=live),
    }


def ledger_payload(
    *,
    owner: str,
    fund: str | None,
    live: bool,
) -> dict[str, Any]:
    fund_filter = None if not fund or fund.lower() == "all" else fund
    conn = connect_db(trades_cli._db_path())
    summaries: list[str] = []
    positions_out: list[dict[str, Any]] = []
    try:
        init_trade_schema(conn)
        if fund_filter:
            fund_row = get_fund_by_name(conn, fund_filter)
            if fund_row:
                marks = marks_for_fund(
                    conn, fund_row, date.today(), live=live, force_live=live
                )
                result = build_fund_ledger_with_db(
                    conn, fund_row, marks, date.today(), strict=False
                )
                for p in sorted(result.positions, key=lambda x: -(x.pct_of_nav or 0)):
                    if p.shares <= 0:
                        continue
                    from ledger.option_symbols import display_option_label

                    label = p.ticker
                    if p.instrument_type == "option":
                        label = display_option_label(
                            underlying=p.underlying,
                            expiry=p.expiry,
                            strike=p.strike,
                            put_call=p.put_call,
                            occ_symbol=p.ticker,
                        )
                    positions_out.append(
                        {
                            "ticker": p.ticker,
                            "display_name": label,
                            "instrument_type": p.instrument_type,
                            "shares": p.shares,
                            "avg_cost": p.avg_cost,
                            "mark_price": p.mark_price,
                            "market_value": p.market_value,
                            "unrealized_pl": p.unrealized_pl,
                            "realized_pl": p.cumulative_realized_pl,
                            "currency": fund_row.currency,
                        }
                    )
                summaries.append(
                    f"Fund “{fund_filter}”: cash {result.cash_balance:,.2f} · "
                    f"NAV {result.nav:,.2f} · ROI {result.fund_roi_pct or 0:.2f}%"
                )
        else:
            for pos in collect_ledger_card_positions(
                conn, owner=owner, fund_name=None, live=live, force_live=live
            ):
                positions_out.append(
                    {
                        "ticker": pos.ticker,
                        "display_name": pos.display_name or pos.ticker,
                        "instrument_type": pos.instrument_type,
                        "shares": pos.shares,
                        "avg_cost": pos.avg_cost,
                        "mark_price": pos.mark_price,
                        "market_value": pos.market_value,
                        "unrealized_pl": pos.unrealized_pl,
                        "realized_pl": None,
                        "currency": pos.currency,
                    }
                )
            funds = (
                list_funds(conn)
                if owner.strip().lower() == "all"
                else [f for f in list_funds(conn) if f.owner == owner]
            )
            for f in funds:
                marks = marks_for_fund(
                    conn, f, date.today(), live=live, force_live=live
                )
                result = build_fund_ledger_with_db(
                    conn, f, marks, date.today(), strict=False
                )
                summaries.append(
                    f"Fund “{f.name}”: cash {result.cash_balance:,.2f} · "
                    f"NAV {result.nav:,.2f} · ROI {result.fund_roi_pct or 0:.2f}%"
                )
    finally:
        conn.close()

    disc = compare_ledger_to_sheet(
        trades_cli._db_path(),
        owner=owner,
        fund_name=fund_filter,
    )
    return {
        "summaries": summaries,
        "positions": positions_out,
        "discrepancies": [_jsonable(d) for d in disc],
    }


def heatmap_payload(
    *,
    owner: str,
    fund: str | None,
    color_by: str,
    live: bool,
) -> dict[str, Any]:
    from reports.heatmap_data import collect_heatmap_tiles, tile_pl_for_color
    from ledger.quotes import sum_tiles_value_usd

    owner_arg = None if owner.lower() == "all" else owner
    fund_arg = None if not fund or fund.lower() == "all" else fund
    color = "day" if color_by.lower().startswith("day") else "overall"
    tiles = collect_heatmap_tiles(
        trades_cli._db_path(),
        owner=owner_arg,
        fund_name=fund_arg,
        live=live,
    )
    total_usd = sum_tiles_value_usd(tiles, force_fx=live)
    return {
        "total_usd": total_usd,
        "color_by": color,
        "tiles": [
            {
                "ticker": t.ticker,
                "fund_name": t.fund_name,
                "owner": t.owner,
                "value": t.value,
                "pl_pct": tile_pl_for_color(t, color),
                "shares": t.shares,
                "currency": t.currency,
            }
            for t in tiles
        ],
    }


def movers_payload(market: str) -> dict[str, Any]:
    from ledger.market_movers import fetch_market_movers

    mkt = "AU" if market.lower().startswith("aust") else "US"
    try:
        gainers, losers = fetch_market_movers(mkt)
    except Exception:
        # Unlike quotes.py's yfinance calls, fetch_market_movers has no
        # per-symbol try/except of its own — a screener query has nothing to
        # fall back to, so on failure (e.g. Yahoo's query endpoints blocked by
        # a host's outbound allowlist) just return empty rather than a 500.
        return {"market": mkt, "gainers": [], "losers": [], "error": "unavailable"}
    return {
        "market": mkt,
        "gainers": [_jsonable(g) for g in gainers],
        "losers": [_jsonable(l) for l in losers],
    }


def meta_payload(user: "WebUser | None" = None) -> dict[str, Any]:
    from web.auth import allowed_owner_names

    owners = allowed_owner_names(user) if user is not None else ["All", *_OWNERS]
    return {
        "owners": owners,
        "markets": ["All", "Australia", "United States"],
        "currencies": ["USD", "AUD", "GBP"],
        "live_quotes_enabled": live_quotes_enabled(),
        "username": user.username if user else None,
        "is_admin": user.is_admin if user else True,
    }


def fund_balances_payload(owner: str) -> dict[str, Any]:
    """Current fund balances (manual override if set, else from ledger)."""
    from reports.net_worth import compute_owner_net_worth

    owner = owner.strip()
    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        net_worth = compute_owner_net_worth(conn, owner, use_live=False)
        balances = []
        for line in net_worth.lines:
            if line.kind == "fund":
                balances.append(
                    {
                        "fund_name": line.label,
                        "currency": line.currency,
                        "amount": line.amount_local,
                        "amount_usd": line.amount_usd,
                        "as_of": line.as_of,
                        "source": line.source,
                    }
                )
        return {
            "owner": owner,
            "balances": balances,
            "total_usd": sum(b["amount_usd"] for b in balances),
        }
    finally:
        conn.close()


def net_worth_payload(owner: str) -> dict[str, Any]:
    """Owner-level net worth breakdown (funds + other assets)."""
    from reports.net_worth import compute_owner_net_worth

    owner = owner.strip()
    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        net_worth = compute_owner_net_worth(conn, owner, use_live=False)
        return {
            "owner": owner,
            "lines": [
                {
                    "label": line.label,
                    "kind": line.kind,
                    "currency": line.currency,
                    "amount_local": line.amount_local,
                    "amount_usd": line.amount_usd,
                    "as_of": line.as_of,
                    "source": line.source,
                }
                for line in net_worth.lines
            ],
            "total_usd": net_worth.total_usd,
            "fx_rates": net_worth.fx_rates,
        }
    finally:
        conn.close()


def other_assets_payload(owner: str) -> dict[str, Any]:
    """List other assets (non-fund holdings) for owner."""
    from ledger.trades_db import fetch_owner_other_assets

    owner = owner.strip()
    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        assets = fetch_owner_other_assets(conn, owner)
        return {
            "owner": owner,
            "assets": [
                {
                    "owner": a.owner,
                    "asset_type": a.asset_type,
                    "amount": a.amount,
                    "currency": a.currency,
                    "updated_at": a.updated_at,
                    "notes": a.notes,
                }
                for a in assets
            ],
        }
    finally:
        conn.close()


def fund_trends_payload(owner: str, fund: str | None = None) -> dict[str, Any]:
    """Historical fund balance data for charting.

    Mirrors the desktop GUI's fund-trends tab (trade_gui.py), which calls
    collect_fund_balance_series with the same defaults (monthly statements mode).
    """
    from reports.fund_balance_history import collect_fund_balance_series
    from ledger.quotes import fx_to_usd_rates

    owner = owner.strip()
    owner_arg = None if owner.lower() == "all" else owner
    fund_arg = fund.strip() if fund and fund.strip().lower() != "all" else None

    fx = fx_to_usd_rates(allow_network=False)
    series_list = collect_fund_balance_series(
        trades_cli._db_path(),
        owner=owner_arg,
        fund_name=fund_arg,
    )

    all_series = []
    for series in series_list:
        if not series.points:
            continue
        nav_local = [p.nav for p in series.points]
        all_series.append(
            {
                "fund_name": series.fund_name,
                "owner": series.owner,
                "currency": series.currency,
                "dates": [p.as_of.isoformat() for p in series.points],
                "nav_local": nav_local,
                "nav_usd": [n * fx.get(series.currency.upper(), 1.0) for n in nav_local],
            }
        )
    return {"owner": owner, "fund": fund, "series": all_series}


def update_fund_manual_balance(
    fund_name: str, nav: float, balance_date: date
) -> dict[str, Any]:
    """Set or update manual balance override for a fund."""
    from ledger.trades_db import upsert_fund_manual_balance

    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        fund = get_fund_by_name(conn, fund_name)
        if not fund:
            raise ValueError(f"Unknown fund {fund_name!r}")
        upsert_fund_manual_balance(conn, fund.id, balance_date, nav)
        return {"fund_name": fund_name, "nav": nav, "balance_date": balance_date.isoformat()}
    finally:
        conn.close()


def create_other_asset(
    owner: str, asset_type: str, amount: float, currency: str
) -> dict[str, Any]:
    """Create or update other asset entry (tracked by owner+type)."""
    from ledger.trades_db import upsert_owner_other_asset

    owner = owner.strip()
    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        upsert_owner_other_asset(conn, owner, asset_type, amount, currency=currency)
        return {
            "owner": owner,
            "asset_type": asset_type,
            "amount": amount,
            "currency": currency,
        }
    finally:
        conn.close()


def delete_other_asset(owner: str, asset_type: str) -> dict[str, Any]:
    """Delete an other asset entry."""
    from ledger.trades_db import delete_owner_other_asset

    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        deleted = delete_owner_other_asset(conn, owner, asset_type)
        if not deleted:
            raise ValueError(f"Asset not found: {owner} / {asset_type}")
        return {"owner": owner, "asset_type": asset_type, "deleted": True}
    finally:
        conn.close()
