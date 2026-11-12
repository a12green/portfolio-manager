from __future__ import annotations

from datetime import date
from typing import Any

from ledger.quotes import convert_to_usd, fx_to_usd_rates, live_quotes_enabled, marks_for_fund
from ledger.trade_ledger import build_fund_ledger_with_db
from ledger.trades_db import fetch_fund_performance_history, init_trade_schema, list_funds


def _usd(amount: float, currency: str, rates: dict[str, float]) -> float:
    return convert_to_usd(amount, currency, rates)


def _funds_for_owner(conn, owner: str):
    init_trade_schema(conn)
    return [f for f in list_funds(conn) if f.owner == owner]


def collect_ledger_fund_lines(
    conn,
    owner: str,
    as_of: date | None = None,
    *,
    live: bool | None = None,
) -> list[dict[str, Any]]:
    """Per-fund metrics from trade ledgers (USD), shaped for portfolio PDF charts."""
    as_of = as_of or date.today()
    use_live = live if live is not None else live_quotes_enabled()
    rates = fx_to_usd_rates(force=use_live)
    lines: list[dict[str, Any]] = []

    for fund in _funds_for_owner(conn, owner):
        marks = marks_for_fund(conn, fund, as_of, live=use_live, force_live=use_live)
        result = build_fund_ledger_with_db(conn, fund, marks, as_of, strict=False)
        invested = _usd(result.invested_capital, fund.currency, rates)
        cash = _usd(result.cash_balance, fund.currency, rates)
        positions = _usd(result.positions_value, fund.currency, rates)
        nav = _usd(result.nav, fund.currency, rates)
        lines.append(
            {
                "fund_name": fund.name,
                "cash_invested": invested,
                "current_cash_balance": cash,
                "active_purchase_amount": positions,
                "fund_total_usd": nav,
                "total_profit_usd": nav - invested,
            }
        )

    return sorted(lines, key=lambda r: -(r.get("fund_total_usd") or 0))


def collect_ledger_holdings(
    conn,
    owner: str,
    as_of: date | None = None,
    *,
    live: bool | None = None,
) -> list[dict[str, Any]]:
    """Open positions from trade ledgers, merged by ticker, values in USD."""
    as_of = as_of or date.today()
    use_live = live if live is not None else live_quotes_enabled()
    rates = fx_to_usd_rates(force=use_live)
    merged: dict[str, dict[str, float]] = {}

    for fund in _funds_for_owner(conn, owner):
        marks = marks_for_fund(conn, fund, as_of, live=use_live, force_live=use_live)
        result = build_fund_ledger_with_db(conn, fund, marks, as_of, strict=False)
        for p in result.positions:
            if p.market_value is None or p.market_value <= 0:
                continue
            ticker = p.ticker.upper()
            mkt = _usd(p.market_value, fund.currency, rates)
            cost = _usd(p.cost_basis, fund.currency, rates)
            bucket = merged.setdefault(ticker, {"mkt_value": 0.0, "cost_basis": 0.0})
            bucket["mkt_value"] += mkt
            bucket["cost_basis"] += cost

    out: list[dict[str, Any]] = []
    for ticker, vals in merged.items():
        cost = vals["cost_basis"]
        mkt = vals["mkt_value"]
        pl = mkt - cost
        out.append(
            {
                "ticker": ticker,
                "mkt_value": mkt,
                "cost_basis": cost,
                "pl_dollar": pl,
                "pl_pct": (pl / cost * 100.0) if cost > 0 else 0.0,
            }
        )
    return sorted(out, key=lambda h: -h["mkt_value"])


def _aggregate_performance_history(
    conn,
    owner: str,
) -> list[tuple[date, float, float, float]]:
    """Sum fund_performance_snapshots per date: positions, NAV, profit (USD)."""
    rates = fx_to_usd_rates()
    by_date: dict[date, dict[str, float]] = {}

    for fund in _funds_for_owner(conn, owner):
        for row in fetch_fund_performance_history(conn, fund.id):
            d = date.fromisoformat(str(row["snapshot_date"]))
            cur = fund.currency
            pos = _usd(float(row["positions_value"]), cur, rates)
            nav = _usd(float(row["nav"]), cur, rates)
            invested = _usd(float(row.get("invested_capital") or 0), cur, rates)
            profit = nav - invested if invested > 0 else _usd(
                float(row.get("cumulative_realized_pl") or 0), cur, rates
            )
            bucket = by_date.setdefault(d, {"pos": 0.0, "nav": 0.0, "profit": 0.0})
            bucket["pos"] += pos
            bucket["nav"] += nav
            bucket["profit"] += profit

    return sorted(
        ((d, v["pos"], v["nav"], v["profit"]) for d, v in by_date.items()),
        key=lambda x: x[0],
    )


def _latest_two(values: list[tuple[date, float]]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    latest = values[-1][1]
    prev = values[-2][1] if len(values) >= 2 else None
    return latest, prev


def collect_ledger_owner_series(conn, owner: str) -> dict[str, Any]:
    """
    Time series and latest KPIs for an owner, all from trade-fund data.
    """
    hist = _aggregate_performance_history(conn, owner)
    pos_pts = [(d, pos) for d, pos, _, _ in hist]
    fund_pts = [(d, nav, profit) for d, _, nav, profit in hist]

    latest_pos, prev_pos = _latest_two(pos_pts)
    latest_nav, prev_nav = _latest_two([(d, nav) for d, _, nav, _ in hist])
    latest_profit, prev_profit = _latest_two([(d, p) for d, _, _, p in hist])

    # Current ledger may be newer than last stored snapshot — prefer live totals.
    lines = collect_ledger_fund_lines(conn, owner)
    if lines:
        latest_pos = sum(r["active_purchase_amount"] for r in lines)
        latest_nav = sum(r["fund_total_usd"] for r in lines)
        latest_profit = sum(r["total_profit_usd"] for r in lines)

    return {
        "positions_pts": pos_pts,
        "fund_pts": fund_pts,
        "latest_positions": latest_pos,
        "prev_positions": prev_pos,
        "latest_nav": latest_nav,
        "prev_nav": prev_nav,
        "latest_profit": latest_profit,
        "prev_profit": prev_profit,
        "fund_lines": lines,
        "holdings": collect_ledger_holdings(conn, owner),
    }


def collect_all_owner_series(conn) -> dict[str, dict[str, Any]]:
    owners = sorted({f.owner for f in list_funds(conn)})
    return {owner: collect_ledger_owner_series(conn, owner) for owner in owners}
