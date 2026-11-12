"""API helpers — thin wrappers over existing portfolio modules."""

from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import trades_cli
from ledger.db import connect_db
from reports.holdings_compare import collect_ledger_card_positions
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
    from app.use_cases import ledger_detail

    detail = ledger_detail(trades_cli._db_path(), owner=owner, fund=fund, live=live)
    summaries = [
        f"Fund “{f.name}”: cash {f.cash:,.2f} · NAV {f.nav:,.2f} · ROI {f.roi_pct or 0:.2f}%"
        for f in detail.funds
    ]
    return {
        "summaries": summaries,
        "positions": [
            {
                "ticker": p.ticker,
                "display_name": p.display_name,
                "instrument_type": p.instrument_type,
                "shares": p.shares,
                "avg_cost": p.avg_cost,
                "mark_price": p.mark_price,
                "market_value": p.market_value,
                "unrealized_pl": p.unrealized_pl,
                "realized_pl": p.realized_pl,
                "currency": p.currency,
            }
            for p in detail.positions
        ],
    }


def heatmap_payload(
    *,
    owner: str,
    fund: str | None,
    color_by: str,
    live: bool,
) -> dict[str, Any]:
    from app.use_cases import heatmap
    from reports.heatmap_data import tile_pl_for_color

    view = heatmap(trades_cli._db_path(), owner=owner, fund=fund, color_by=color_by, live=live)
    return {
        "total_usd": view.total_usd,
        "color_by": view.color_by,
        "tiles": [
            {
                "ticker": t.ticker,
                "fund_name": t.fund_name,
                "owner": t.owner,
                "value": t.value,
                "pl_pct": tile_pl_for_color(t, view.color_by),
                "shares": t.shares,
                "currency": t.currency,
            }
            for t in view.tiles
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
    from app.use_cases import net_worth

    owner = owner.strip()
    nw = net_worth(trades_cli._db_path(), owner=owner, use_live=False)
    balances = [
        {
            "fund_name": line.label,
            "currency": line.currency,
            "amount": line.amount_local,
            "amount_usd": line.amount_usd,
            "as_of": line.as_of,
            "source": line.source,
        }
        for line in nw.lines
        if line.kind == "fund"
    ]
    return {
        "owner": owner,
        "balances": balances,
        "total_usd": sum(b["amount_usd"] for b in balances),
    }


def net_worth_payload(owner: str) -> dict[str, Any]:
    """Owner-level net worth breakdown (funds + other assets)."""
    from app.use_cases import net_worth

    owner = owner.strip()
    nw = net_worth(trades_cli._db_path(), owner=owner, use_live=False)
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
            for line in nw.lines
        ],
        "total_usd": nw.total_usd,
        "fx_rates": nw.fx_rates,
    }


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
    """Historical fund balance data for charting (monthly statements mode, as in the GUI)."""
    from app.use_cases import fund_trends, series_nav_usd
    from ledger.quotes import fx_to_usd_rates

    owner = owner.strip()
    fx = fx_to_usd_rates(allow_network=False)
    all_series = []
    for series in fund_trends(trades_cli._db_path(), owner=owner, fund=fund):
        if not series.points:
            continue
        all_series.append(
            {
                "fund_name": series.fund_name,
                "owner": series.owner,
                "currency": series.currency,
                "dates": [p.as_of.isoformat() for p in series.points],
                "nav_local": [p.nav for p in series.points],
                "nav_usd": series_nav_usd(series, fx),
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
