from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from ledger.db import connect_db
from ledger.trade_ledger import build_fund_ledger
from ledger.trades_db import (
    TradeFund,
    fetch_trades_for_fund,
    init_trade_schema,
    list_funds,
)

_PORTFOLIO_OWNERS = (OWNER_A, OWNER_B, OWNER_C)
HeatmapColorBy = Literal["overall", "day"]
_holdings_tiles_cache: dict[str, tuple[float, tuple[HeatmapTile, ...]]] = {}
_HOLDINGS_CACHE_TTL_SEC = 300.0


def clear_heatmap_caches() -> None:
    _holdings_tiles_cache.clear()


@dataclass(frozen=True)
class HeatmapTile:
    ticker: str
    fund_name: str
    owner: str
    value: float
    overall_pl_pct: float
    day_pl_pct: float
    shares: float
    currency: str


def tile_pl_for_color(tile: HeatmapTile, color_by: HeatmapColorBy = "overall") -> float:
    if color_by == "day":
        return tile.day_pl_pct
    return tile.overall_pl_pct


def _tile_value(market_value: float | None, cost_basis: float) -> float:
    if market_value is not None and market_value > 0:
        return float(market_value)
    if cost_basis > 0:
        return float(cost_basis)
    return 0.0


def _tile_pl_pct(unrealized_roi_pct: float | None, unrealized_pl: float | None, cost_basis: float) -> float:
    if unrealized_roi_pct is not None:
        return float(unrealized_roi_pct)
    if unrealized_pl is not None and cost_basis > 0:
        return float(unrealized_pl / cost_basis * 100.0)
    return 0.0


def collect_heatmap_tiles(
    db_path: Path,
    *,
    owner: str | None = None,
    fund_name: str | None = None,
    as_of: date | None = None,
    live: bool = False,
    color_by: HeatmapColorBy = "overall",
) -> list[HeatmapTile]:
    """Open positions from trade ledgers, filtered by owner and/or fund."""
    as_of = as_of or date.today()
    owner_filter = owner.strip() if owner and owner.strip().lower() != "all" else None
    fund_filter = fund_name.strip() if fund_name and fund_name.strip().lower() != "all" else None

    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        all_funds = list_funds(conn)

        funds = list(all_funds)
        if fund_filter:
            funds = [f for f in funds if f.name == fund_filter]
        if owner_filter:
            funds = [f for f in funds if f.owner == owner_filter]

        tiles: list[HeatmapTile] = []
        for fund in funds:
            tiles.extend(_tiles_for_fund(conn, fund, as_of, live=live))
    finally:
        conn.close()

    # OWNER_C (and similar) often live only in the holdings file — no trade fund yet.
    if not fund_filter:
        owners_for_file = [owner_filter] if owner_filter else list(_PORTFOLIO_OWNERS)
        for o in owners_for_file:
            if not any(f.owner == o for f in all_funds):
                tiles.extend(_tiles_from_holdings_file(o))

    if not fund_filter and len(funds) > 1:
        tiles = aggregate_tiles_by_ticker(tiles)
    elif not fund_filter and owner_filter and len(tiles) > 1:
        tiles = aggregate_tiles_by_ticker(tiles)

    # Live marks are applied in _tiles_for_fund via fetch_mark_prices_merged.

    if color_by == "day" or live:
        tiles = _apply_day_change_pct(tiles, db_path, force=live)

    tiles.sort(key=lambda t: (-t.value, t.ticker))
    return tiles


def _apply_live_quotes(
    tiles: list[HeatmapTile],
    db_path: Path,
    *,
    force: bool = False,
) -> list[HeatmapTile]:
    from ledger.quotes import convert_from_usd, fetch_live_marks_for_owner, live_quotes_enabled, normalize_ticker

    if not tiles or not live_quotes_enabled(force=force):
        return tiles

    owners = {t.owner for t in tiles if t.owner not in ("All", "")}
    live: dict[str, float] = {}
    for o in sorted(owners):
        live.update(fetch_live_marks_for_owner(db_path, o, force=force))

    if not live:
        return tiles

    out: list[HeatmapTile] = []
    for t in tiles:
        mark = live.get(normalize_ticker(t.ticker))
        if mark is None or t.shares <= 0:
            out.append(t)
            continue
        cur = (t.currency or "USD").strip().upper()
        if cur not in ("USD", "US"):
            mark = convert_from_usd(mark, cur)
        cost_basis = (
            t.value / (1.0 + t.overall_pl_pct / 100.0) if t.overall_pl_pct != 0 else t.value
        )
        new_value = t.shares * mark
        overall = ((new_value - cost_basis) / cost_basis * 100.0) if cost_basis > 0 else 0.0
        out.append(
            HeatmapTile(
                ticker=t.ticker,
                fund_name=t.fund_name,
                owner=t.owner,
                value=new_value,
                overall_pl_pct=overall,
                day_pl_pct=t.day_pl_pct,
                shares=t.shares,
                currency=t.currency,
            )
        )
    return out


def _apply_day_change_pct(
    tiles: list[HeatmapTile],
    db_path: Path,
    *,
    force: bool = False,
) -> list[HeatmapTile]:
    from ledger.db import connect_db
    from ledger.quotes import fetch_owner_marks_with_currency, fetch_quote_details, listing_currency, normalize_ticker

    if not tiles:
        return tiles

    owners = {t.owner for t in tiles if t.owner not in ("All", "")}
    mark_currencies: dict[tuple[str, str], str] = {}
    conn = connect_db(db_path)
    try:
        for owner in owners:
            for norm, (_price, cur) in fetch_owner_marks_with_currency(conn, owner, live=False).items():
                mark_currencies[(owner, norm)] = cur
    finally:
        conn.close()

    symbols: list[tuple[str, str]] = []
    for t in tiles:
        norm = normalize_ticker(t.ticker)
        owner = t.owner if t.owner not in ("All", "") else ""
        cur = mark_currencies.get((owner, norm)) if owner else None
        if not cur:
            cur = listing_currency(t.ticker, fund_currency=t.currency)
        symbols.append((t.ticker, cur))

    cache_key = f"heatmap:day:{','.join(sorted({normalize_ticker(t) for t, _ in symbols}))}"
    try:
        details = fetch_quote_details(symbols, cache_key=cache_key[:200], force=force)
    except Exception:
        details = {}

    out: list[HeatmapTile] = []
    for t in tiles:
        d = details.get(normalize_ticker(t.ticker))
        day_pct = float(d.day_change_pct) if d and d.day_change_pct is not None else 0.0
        out.append(
            HeatmapTile(
                ticker=t.ticker,
                fund_name=t.fund_name,
                owner=t.owner,
                value=t.value,
                overall_pl_pct=t.overall_pl_pct,
                day_pl_pct=day_pct,
                shares=t.shares,
                currency=t.currency,
            )
        )
    return out


def aggregate_tiles_by_ticker(tiles: list[HeatmapTile]) -> list[HeatmapTile]:
    """Merge the same ticker across funds into one tile (summed size, blended P/L %)."""
    from ledger.quotes import convert_to_usd, fx_to_usd_rates

    rates = fx_to_usd_rates()
    by_ticker: dict[str, list[HeatmapTile]] = {}
    for t in tiles:
        by_ticker.setdefault(t.ticker.upper(), []).append(t)

    merged: list[HeatmapTile] = []
    for ticker, parts in by_ticker.items():
        currencies = {p.currency for p in parts}
        multi_ccy = len(currencies) > 1
        if multi_ccy:
            total_value = sum(convert_to_usd(p.value, p.currency, rates) for p in parts)
            total_cost = sum(
                convert_to_usd(p.value / (1.0 + p.overall_pl_pct / 100.0), p.currency, rates)
                for p in parts
            )
            currency = "USD"
        else:
            currency = currencies.pop() if currencies else "USD"
            total_value = sum(p.value for p in parts)
            total_cost = sum(
                p.value / (1.0 + p.overall_pl_pct / 100.0) for p in parts
            )
        if total_value <= 0:
            continue
        overall = ((total_value - total_cost) / total_cost * 100.0) if total_cost > 0 else 0.0
        if multi_ccy:
            vals = [convert_to_usd(p.value, p.currency, rates) for p in parts]
            day_pl = sum(v * p.day_pl_pct for v, p in zip(vals, parts)) / sum(vals) if sum(vals) else 0.0
        else:
            vals = [p.value for p in parts]
            day_pl = sum(v * p.day_pl_pct for v, p in zip(vals, parts)) / sum(vals) if sum(vals) else 0.0
        owners = {p.owner for p in parts}
        funds = {p.fund_name for p in parts}
        merged.append(
            HeatmapTile(
                ticker=ticker,
                fund_name=funds.pop() if len(funds) == 1 else f"{len(funds)} funds",
                owner=owners.pop() if len(owners) == 1 else "All",
                value=total_value,
                overall_pl_pct=overall,
                day_pl_pct=day_pl,
                shares=sum(p.shares for p in parts),
                currency=currency,
            )
        )
    return merged


def _normalize_holdings_ticker(raw: str) -> str:
    s = str(raw).strip().upper()
    if ":" in s:
        s = s.rsplit(":", 1)[-1]
    return s


def _currency_from_location(loc: str) -> str:
    loc = str(loc).strip().upper()
    if loc in ("AUS", "AUD", "AU"):
        return "AUD"
    return "USD"


def _tiles_from_holdings_file(owner: str) -> list[HeatmapTile]:
    """Holdings from the holdings file (Stocks.csv) when no trade ledger exists for owner."""
    now = time.time()
    cached = _holdings_tiles_cache.get(owner)
    if cached is not None:
        ts, tiles = cached
        if now - ts < _HOLDINGS_CACHE_TTL_SEC:
            return list(tiles)

    try:
        from main import _prepare_holdings, load_portfolio_dataframe

        work = _prepare_holdings(load_portfolio_dataframe())
    except Exception:
        return []

    sub = work[work["Portfolio"] == owner]
    out: list[HeatmapTile] = []
    for _, row in sub.iterrows():
        value = float(row["mkt_value"])
        if value <= 0:
            continue
        out.append(
            HeatmapTile(
                ticker=_normalize_holdings_ticker(row["Ticker"]),
                fund_name=f"{owner} portfolio",
                owner=owner,
                value=value,
                overall_pl_pct=float(row["pl_pct"]),
                day_pl_pct=0.0,
                shares=float(row["Shares"]),
                currency=_currency_from_location(str(row.get("Location", ""))),
            )
        )
    _holdings_tiles_cache[owner] = (now, tuple(out))
    return out


def _tiles_for_fund(
    conn,
    fund: TradeFund,
    as_of: date,
    *,
    live: bool = False,
) -> list[HeatmapTile]:
    from ledger.quotes import marks_for_fund

    trades = fetch_trades_for_fund(conn, fund.id)
    ledger_marks = marks_for_fund(conn, fund, as_of, live=live, force_live=live)
    result = build_fund_ledger(fund, trades, ledger_marks, as_of, strict=False)

    out: list[HeatmapTile] = []
    for p in result.positions:
        value = _tile_value(p.market_value, p.cost_basis)
        if value <= 0:
            continue
        out.append(
            HeatmapTile(
                ticker=p.ticker,
                fund_name=fund.name,
                owner=fund.owner,
                value=value,
                overall_pl_pct=_tile_pl_pct(p.unrealized_roi_pct, p.unrealized_pl, p.cost_basis),
                day_pl_pct=0.0,
                shares=p.shares,
                currency=fund.currency,
            )
        )
    return out


def group_tiles_by_fund(tiles: list[HeatmapTile]) -> dict[str, list[HeatmapTile]]:
    groups: dict[str, list[HeatmapTile]] = {}
    for t in tiles:
        groups.setdefault(t.fund_name, []).append(t)
    for fund in groups:
        groups[fund].sort(key=lambda x: -x.value)
    return dict(sorted(groups.items(), key=lambda kv: -sum(t.value for t in kv[1])))
