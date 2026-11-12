from __future__ import annotations

import time
from bisect import bisect_right
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from ledger.db import connect_db
from ledger.trade_ledger import build_fund_ledger
from ledger.trades_db import (
    TradeFund,
    delete_fund_performance_snapshots,
    fetch_fund_cash_events,
    fetch_fund_manual_balances,
    fetch_fund_performance_history,
    fetch_fund_reported_cash,
    fetch_fund_ticker_marks,
    fetch_mark_prices_for_owner,
    fetch_trades_for_fund,
    get_fund_by_name,
    init_trade_schema,
    list_funds,
    upsert_fund_performance_snapshot,
)

TrendsMode = Literal["statements", "transactions", "manual"]

_SERIES_CACHE_TTL_SEC = 300.0
_series_cache: dict[str, tuple[float, list]] = {}


@dataclass(frozen=True)
class BalancePoint:
    as_of: date
    nav: float
    cash: float
    positions: float
    source: str
    invested: float = 0.0


@dataclass(frozen=True)
class FundBalanceSeries:
    fund_name: str
    owner: str
    currency: str
    location: str
    points: tuple[BalancePoint, ...]


def combine_fund_balance_series(
    series: tuple[FundBalanceSeries, ...] | list[FundBalanceSeries],
    *,
    label: str = "Total",
    owner: str = "All",
) -> FundBalanceSeries | None:
    """Sum NAV and invested across funds (USD when currencies differ)."""
    if len(series) <= 1:
        return None

    from ledger.quotes import convert_to_usd, fx_to_usd_rates

    rates = fx_to_usd_rates()
    currencies = {str(s.currency or "USD").upper() for s in series}
    currency = currencies.pop() if len(currencies) == 1 else "USD"
    mixed = len(currencies) > 0

    all_dates = sorted({p.as_of for s in series for p in s.points})
    if not all_dates:
        return None

    points: list[BalancePoint] = []
    for d in all_dates:
        total_nav = 0.0
        total_inv = 0.0
        total_cash = 0.0
        total_pos = 0.0
        seen = False
        for s in series:
            prior = [p for p in s.points if p.as_of <= d]
            if not prior:
                continue
            seen = True
            p = prior[-1]
            cur = str(s.currency or "USD").upper()
            if mixed:
                total_nav += convert_to_usd(p.nav, cur, rates)
                total_inv += convert_to_usd(p.invested, cur, rates)
                total_cash += convert_to_usd(p.cash, cur, rates)
                total_pos += convert_to_usd(p.positions, cur, rates)
            else:
                total_nav += p.nav
                total_inv += p.invested
                total_cash += p.cash
                total_pos += p.positions
        if seen:
            points.append(
                BalancePoint(
                    as_of=d,
                    nav=total_nav,
                    cash=total_cash,
                    positions=total_pos,
                    source="total",
                    invested=total_inv,
                )
            )

    if not points:
        return None

    return FundBalanceSeries(
        fund_name=label,
        owner=owner,
        currency=currency,
        location="All",
        points=tuple(points),
    )


def clear_fund_balance_series_cache() -> None:
    _series_cache.clear()


def currency_to_location(currency: str) -> str:
    c = str(currency or "").upper()
    if c == "AUD":
        return "Australia"
    if c == "GBP":
        return "United Kingdom"
    return "United States"


def matches_location(fund: TradeFund, location: str) -> bool:
    if not location or location == "All":
        return True
    loc = currency_to_location(fund.currency)
    if location.startswith("United States") or location == "US":
        return loc == "United States"
    if location.startswith("Austral"):
        return loc == "Australia"
    if location.startswith("United Kingdom") or location == "UK":
        return loc == "United Kingdom"
    return loc == location


def _series_cache_key(
    db_path,
    owner: str | None,
    fund_name: str | None,
    location: str,
    trends_mode: TrendsMode,
) -> str:
    try:
        mtime = Path(db_path).stat().st_mtime
    except OSError:
        mtime = 0.0
    return "|".join(
        [
            str(db_path),
            str(mtime),
            owner or "All",
            fund_name or "All",
            location or "All",
            trends_mode,
        ]
    )


def _month_end_dates(start: date, end: date) -> list[date]:
    """Last calendar day of each month from start through end."""
    if start > end:
        return []
    out: set[date] = set()
    y, m = start.year, start.month
    while date(y, m, 1) <= end.replace(day=1):
        last = date(y, m, monthrange(y, m)[1])
        if start <= last <= end:
            out.add(last)
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    if end >= start:
        out.add(end)
    return sorted(out)


# HSA opening balance came from a transfer of assets — pool only, not invested.
_OPENING_IS_TRANSFER_ONLY = frozenset({"Fidelity HSA"})


def _opening_adds_to_invested(fund: TradeFund) -> bool:
    return fund.name not in _OPENING_IS_TRANSFER_ONLY


def _is_eft_received(note: str, amount: float) -> bool:
    n = (note or "").upper()
    return amount > 0 and (
        "ELECTRONIC FUNDS TRANSFER RECEIVED" in n
        or ("ELECTRONIC FUNDS TRANSFER" in n and "PAID" not in n)
    )


def _is_eft_paid(note: str, amount: float) -> bool:
    n = (note or "").upper()
    return amount < 0 and (
        "ELECTRONIC FUNDS TRANSFER PAID" in n
        or ("ELECTRONIC FUNDS TRANSFER" in n and "RECEIVED" not in n)
    )


def _is_account_transfer(note: str) -> bool:
    """In-kind or between-account moves — not a bank deposit."""
    n = (note or "").upper()
    if "ELECTRONIC FUNDS TRANSFER" in n:
        return False
    if "TRANSFER OF ASSETS" in n:
        return True
    if "TRANSFERRED TO" in n or "TRANSFERRED FROM" in n:
        return True
    return "TRANSFER" in n


def _cash_event_pool_delta(evt) -> float:
    """Cash available for buys."""
    note = (evt.notes or "").upper()
    amount = float(evt.amount)
    if "REINVEST" in note:
        return 0.0
    if _is_eft_received(note, amount) or _is_eft_paid(note, amount):
        return amount
    if amount < 0:
        if "DEBIT CARD" in note or any(
            x in note for x in ("WITHDRAWAL", "WIRE OUT", "DISTRIBUTION", "CHECK PAID")
        ):
            return amount
        return 0.0
    if "DIVIDEND RECEIVED" in note or "INTEREST" in note:
        return amount
    if _is_account_transfer(note):
        return amount
    if any(x in note for x in ("CONTRIBUTION", "DEPOSIT", "CHECK RECEIVED", "WIRE IN")):
        return amount
    return 0.0


def _cash_flow_invested_delta(evt) -> float:
    """Deposits, contributions, EFT received; not account/asset transfers."""
    note = (evt.notes or "").upper()
    amount = float(evt.amount)
    if _is_account_transfer(note):
        return 0.0
    if _is_eft_received(note, amount):
        return amount
    if _is_eft_paid(note, amount):
        return amount
    if amount < 0:
        if any(x in note for x in ("WITHDRAWAL", "WIRE OUT", "DISTRIBUTION", "CHECK PAID")):
            return amount
        return 0.0
    if "DIVIDEND RECEIVED" in note or "INTEREST" in note or "REINVEST" in note:
        return 0.0
    if any(x in note for x in ("CONTRIBUTION", "DEPOSIT", "WIRE IN")):
        return amount
    return 0.0


def cumulative_cash_invested(
    fund: TradeFund,
    trades,
    cash_events,
    as_of: date,
) -> float:
    """
    Net external cash contributed from bank deposits and explicit withdrawals.

    Counts EFT received/paid, contributions, and opening cash (when that
    opening balance is a real deposit). Does not infer investment from share
    buys or statement NAV.
    """
    _ = trades
    invested = 0.0
    if _opening_adds_to_invested(fund):
        invested += float(fund.opening_cash)

    for evt in sorted(
        (e for e in cash_events if e.event_date <= as_of),
        key=lambda e: e.event_date,
    ):
        inv = _cash_flow_invested_delta(evt)
        if inv != 0:
            invested += inv

    return invested


def _with_invested(
    fund: TradeFund,
    trades,
    cash_events,
    points: list[BalancePoint],
) -> tuple[BalancePoint, ...]:
    running = 0.0
    out: list[BalancePoint] = []
    for pt in sorted(points, key=lambda p: p.as_of):
        raw = cumulative_cash_invested(fund, trades, cash_events, pt.as_of)
        running = max(running, raw)
        out.append(
            BalancePoint(
                as_of=pt.as_of,
                nav=pt.nav,
                cash=pt.cash,
                positions=pt.positions,
                source=pt.source,
                invested=running,
            )
        )
    return tuple(out)


def _statement_points(conn, fund_id: int) -> list[BalancePoint]:
    points: list[BalancePoint] = []
    for row in fetch_fund_performance_history(conn, fund_id):
        d = date.fromisoformat(str(row["snapshot_date"]))
        points.append(
            BalancePoint(
                as_of=d,
                nav=float(row["nav"]),
                cash=float(row["cash_balance"]),
                positions=float(row["positions_value"]),
                source="statement",
            )
        )
    return points


def _is_cash_only_statement_point(pt: BalancePoint) -> bool:
    """Detect alltx cash-ledger rows stored as NAV (no separate positions value)."""
    if pt.source != "statement":
        return False
    if pt.cash > 1.0:
        return False
    if pt.positions <= 0:
        return True
    return abs(pt.positions - pt.nav) < max(1.0, abs(pt.nav) * 0.001)


def _positive_positions_market_value(
    trades,
    as_of: date,
    hist_resolver,
    currency: str,
) -> tuple[float, bool]:
    """Sum of long holdings × historical marks (trade ledger only).

    Returns (value, fully_covered) — fully_covered is False if any held
    ticker has no Yahoo historical price at this date (long-delisted/renamed
    small-caps predating a corporate action), meaning the value understates
    true positions and must not be trusted as a precise point.
    """
    shares: dict[str, float] = {}
    for tr in trades:
        if tr.trade_date > as_of:
            continue
        tk = tr.ticker.upper()
        delta = tr.shares if tr.side == "buy" else -tr.shares
        shares[tk] = shares.get(tk, 0.0) + delta

    consolidations = _consolidation_events(trades)
    total = 0.0
    fully_covered = True
    for ticker, qty in shares.items():
        if qty <= 1e-6:
            continue
        px = None
        if hist_resolver is not None:
            px = hist_resolver.mark(ticker, currency, as_of)
        if px is None or px <= 0:
            fully_covered = False
            continue
        scale = _pre_consolidation_scale(consolidations, ticker, as_of)
        total += qty * (px / scale)
    return total, fully_covered


def _piecewise_interpolate(anchors: list[tuple[date, float]], d: date) -> float:
    """Linear interpolation between sorted (date, value) anchors; flat outside their range."""
    if not anchors:
        return 0.0
    if d <= anchors[0][0]:
        return anchors[0][1]
    if d >= anchors[-1][0]:
        return anchors[-1][1]
    for (d0, v0), (d1, v1) in zip(anchors, anchors[1:]):
        if d0 <= d <= d1:
            span = (d1 - d0).days
            if span <= 0:
                return v0
            frac = (d - d0).days / span
            return v0 + (v1 - v0) * frac
    return anchors[-1][1]


def rebuild_aussuper_nav_snapshots(
    conn,
    fund: TradeFund,
    alltx_cash_by_month: dict[date, float],
    *,
    statement_navs: dict[date, float] | None = None,
    replace: bool = True,
    current_nav: float = 468_585.89,
    current_positions: float = 338_455.54,
    current_md_cash: float = 69_138.13,
    current_other: float = 60_992.22,
) -> int:
    """
    Rebuild Super A month-end snapshots as total fund value.

    Australian Super has multiple investment options; alltx Balance is Member Direct
    cash only. Annual statement PDFs give total fund NAV at 30 June. Current portal
    totals anchor the latest point (MD market value + cash + other options).

    Member Direct positions are priced per-month from trade-ledger holdings +
    Yahoo historical marks wherever full price coverage exists. Several of Super
    A's older AU small-caps (TPM, WPL pre-rename, TGR, HPI, PGH, MLD, LLL) have
    no Yahoo historical data at all, so those months can't be precisely computed;
    for those, positions are linearly interpolated between the nearest months
    that *do* have full coverage (piecewise, not a single global scale factor —
    a single ratio derived from today's fully-different holdings doesn't
    generalize to eras with completely different holdings composition).

    The "other" pooled-options balance (HG/AS) has no trade-level history at
    all, in any era — it's interpolated between whatever real anchors exist
    (fund inception = $0, annual statement NAVs if supplied, current portal
    total), since holding it at a flat constant across 8+ years is equally
    unfounded and a straight line is the honest "no data" default.
    """
    init_trade_schema(conn)
    trades = fetch_trades_for_fund(conn, fund.id)
    if not trades and not alltx_cash_by_month:
        return 0

    if replace:
        delete_fund_performance_snapshots(conn, fund.id)

    statement_navs = dict(statement_navs or {})
    start = min(
        [t.trade_date for t in trades] + list(alltx_cash_by_month),
        default=date.today(),
    )
    today = date.today()
    dates = sorted(set(_month_end_dates(start, today)) | set(statement_navs))
    if not dates:
        return 0

    from ledger.quotes import HistoricalMarkResolver

    symbols = {(t.ticker, fund.currency) for t in trades}
    hist_resolver = (
        HistoricalMarkResolver.build(list(symbols), start) if symbols else None
    )

    anchor = dates[-1]

    def md_cash_at(d: date) -> float:
        cash = 0.0
        for k, bal in alltx_cash_by_month.items():
            if k.year == d.year and k.month == d.month and k <= d:
                cash = max(cash, bal)
        return cash

    raw_by_date: dict[date, tuple[float, bool]] = {
        d: _positive_positions_market_value(trades, d, hist_resolver, fund.currency)
        for d in dates
    }

    trusted_position_anchors = [
        (d, raw_by_date[d][0]) for d in dates if raw_by_date[d][1]
    ]
    if not trusted_position_anchors:
        trusted_position_anchors = [(dates[0], 0.0), (anchor, current_positions)]

    positions_by_date: dict[date, float] = {}
    for d in dates:
        raw, covered = raw_by_date[d]
        positions_by_date[d] = raw if covered else _piecewise_interpolate(
            trusted_position_anchors, d
        )
    positions_by_date[anchor] = current_positions

    cash_by_date = {d: md_cash_at(d) for d in dates}
    cash_by_date[anchor] = current_md_cash

    other_anchors: list[tuple[date, float]] = [(dates[0], 0.0)]
    for stmt_date, stmt_nav in statement_navs.items():
        derived_other = stmt_nav - cash_by_date.get(stmt_date, md_cash_at(stmt_date)) - positions_by_date.get(
            stmt_date, raw_by_date.get(stmt_date, (0.0, True))[0]
        )
        other_anchors.append((stmt_date, max(derived_other, 0.0)))
    other_anchors.append((anchor, current_other))
    other_anchors.sort(key=lambda pair: pair[0])

    count = 0
    for d in dates:
        cash = cash_by_date[d]
        positions = positions_by_date[d]
        other = _piecewise_interpolate(other_anchors, d)
        nav = current_nav if d == anchor else cash + positions + other

        if nav <= 0:
            continue

        upsert_fund_performance_snapshot(
            conn,
            d,
            fund.id,
            cash_balance=cash,
            positions_value=positions,
            nav=nav,
            invested_capital=0.0,
            cumulative_realized_pl=0.0,
            roi_pct=None,
        )
        count += 1

    clear_fund_balance_series_cache()
    return count


def rebuild_fund_nav_snapshots_from_ledger(
    conn,
    fund: TradeFund,
    *,
    replace: bool = True,
) -> int:
    """
    Rebuild month-end performance snapshots as fund NAV (cash + marked positions),
    not transaction-account cash alone.
    """
    init_trade_schema(conn)
    trades = fetch_trades_for_fund(conn, fund.id)
    cash_events = fetch_fund_cash_events(conn, fund.id)
    if not trades and not cash_events:
        return 0

    if replace:
        delete_fund_performance_snapshots(conn, fund.id)

    start = min(
        [t.trade_date for t in trades] + [e.event_date for e in cash_events],
        default=date.today(),
    )
    month_ends = _month_end_dates(start, date.today())
    if not month_ends:
        return 0

    from ledger.quotes import HistoricalMarkResolver

    symbols = {(t.ticker, fund.currency) for t in trades}
    hist_resolver = (
        HistoricalMarkResolver.build(list(symbols), start) if symbols else None
    )
    reported = fetch_fund_reported_cash(conn, fund.id)
    fund_marks = fetch_fund_ticker_marks(conn, fund.id) or None
    snapshots_by_owner = _load_owner_snapshots(conn, fund.owner)

    count = 0
    for d in month_ends:
        trades_upto = [t for t in trades if t.trade_date <= d]
        events_upto = [e for e in cash_events if e.event_date <= d]
        if not trades_upto and not events_upto:
            continue

        held, last_trade_price = _positions_at(trades, d)
        reported_cash = None
        if reported is not None and reported.balance_date <= d:
            reported_cash = reported.cash_balance

        marks = _marks_as_of(
            trades,
            d,
            fund.currency,
            held=held,
            last_trade_price=last_trade_price,
            hist_resolver=hist_resolver,
            snapshots=snapshots_by_owner,
            today_marks=None,
            fund_marks=fund_marks,
        )
        try:
            result = build_fund_ledger(
                fund,
                trades_upto,
                marks,
                d,
                cash_events=events_upto,
                reported_cash=reported_cash,
                strict=False,
            )
        except Exception:
            continue
        if result.nav <= 0:
            continue

        upsert_fund_performance_snapshot(
            conn,
            d,
            fund.id,
            cash_balance=result.cash_balance,
            positions_value=result.positions_value,
            nav=result.nav,
            invested_capital=0.0,
            cumulative_realized_pl=result.cumulative_realized_pl,
            roi_pct=result.fund_roi_pct,
        )
        count += 1

    clear_fund_balance_series_cache()
    return count


def _load_owner_snapshots(conn, owner: str) -> dict[str, tuple[tuple[date, ...], tuple[float, ...]]]:
    cur = conn.execute(
        """
        SELECT ticker, snapshot_date, last_price
        FROM stock_daily_snapshots
        WHERE owner = ?
        ORDER BY ticker, snapshot_date
        """,
        (owner,),
    )
    raw: dict[str, list[tuple[date, float]]] = {}
    for row in cur.fetchall():
        t = str(row[0]).upper()
        d = date.fromisoformat(str(row[1]))
        raw.setdefault(t, []).append((d, float(row[2])))
    packed: dict[str, tuple[tuple[date, ...], tuple[float, ...]]] = {}
    for ticker, rows in raw.items():
        packed[ticker] = (
            tuple(d for d, _ in rows),
            tuple(px for _, px in rows),
        )
    return packed


def _snapshot_on_or_before(
    snapshots: dict[str, tuple[tuple[date, ...], tuple[float, ...]]],
    ticker: str,
    as_of: date,
) -> float | None:
    dates, prices = snapshots.get(ticker.upper(), ((), ()))
    if not dates:
        return None
    idx = bisect_right(dates, as_of) - 1
    if idx < 0:
        return None
    return prices[idx]


def _mark_from_fund_series(
    fund_marks: dict[str, tuple[tuple[date, ...], tuple[float, ...]]],
    ticker: str,
    as_of: date,
) -> float | None:
    dates, prices = fund_marks.get(ticker.upper(), ((), ()))
    if not dates:
        return None
    idx = bisect_right(dates, as_of) - 1
    if idx < 0:
        return None
    return prices[idx]


def _in_fund_currency(raw_price: float, fund_currency: str) -> float:
    """Convert a raw stock_daily_snapshots price into the fund's reporting currency.

    That table stores prices per owner+ticker with no currency column — always
    the ticker's native listing price (AUD for ASX stocks via the .AX Yahoo
    suffix, USD for everything else; same convention as to_yahoo_symbol()).
    Used unconverted, an AUD/GBP/etc. fund holding a US stock gets the raw USD
    number treated as if it were already local currency.
    """
    from ledger.quotes import fx_to_usd_rates, price_in_fund_currency

    fund_cur = (fund_currency or "USD").strip().upper()
    native_cur = "AUD" if fund_cur in ("AUD", "AUS", "AU") else "USD"
    if native_cur == fund_cur:
        return raw_price
    return price_in_fund_currency(raw_price, native_cur, fund_cur, fx_to_usd_rates())


def _consolidation_events(trades) -> dict[str, list[tuple[date, float]]]:
    """Same-day sell+buy pairs explicitly notated as a share consolidation/split.

    Returns ticker -> [(event_date, old_shares/new_shares), ...] sorted by date.
    Detection is conservative (relies on the "consolidation" note already
    written by the importer for events like the VAU 13:2 in Nov 2025) rather
    than inferring splits from share-count jumps, to avoid misfiring on a
    same-day sell+rebuy that's a real, unrelated trade.
    """
    by_key: dict[tuple[str, date], dict[str, float]] = {}
    for t in trades:
        if "consolidation" not in (t.notes or "").lower():
            continue
        key = (t.ticker.upper(), t.trade_date)
        by_key.setdefault(key, {})[t.side] = t.shares

    events: dict[str, list[tuple[date, float]]] = {}
    for (ticker, event_date), sides in by_key.items():
        old_shares = sides.get("sell")
        new_shares = sides.get("buy")
        if not old_shares or not new_shares:
            continue
        events.setdefault(ticker, []).append((event_date, old_shares / new_shares))
    for ticker in events:
        events[ticker].sort(key=lambda pair: pair[0])
    return events


def _pre_consolidation_scale(
    consolidations: dict[str, list[tuple[date, float]]], ticker: str, as_of: date
) -> float:
    """Yahoo's historical closes are split-adjusted to *today's* share count, so a
    date before a consolidation needs its Yahoo price divided by the product of
    every ratio for consolidations still ahead of it (as_of), to undo that
    adjustment and match the pre-consolidation share count on file for that date.
    """
    scale = 1.0
    for event_date, ratio in consolidations.get(ticker.upper(), ()):
        if as_of < event_date:
            scale *= ratio
    return scale


def _marks_as_of(
    trades,
    as_of: date,
    fund_currency: str,
    *,
    held: set[str],
    last_trade_price: dict[str, float],
    hist_resolver,
    snapshots,
    today_marks: dict[str, float] | None,
    fund_marks: dict[str, tuple[tuple[date, ...], tuple[float, ...]]] | None = None,
) -> dict[str, float]:
    # Open options have no reliable historical price source (Yahoo doesn't serve
    # historical options-chain data), so every source below fell back to the
    # option's purchase-day premium held flat until the closing/expiry trade —
    # a plateau-then-cliff artifact that can swing month-end NAV by thousands.
    # Left unmarked here, they contribute $0 until actually closed, at which
    # point the real trade sets the true proceeds.
    from ledger.option_symbols import is_occ_symbol

    held = {t for t in held if not is_occ_symbol(t)}

    marks: dict[str, float] = {}
    if fund_marks:
        for ticker in held:
            px = _mark_from_fund_series(fund_marks, ticker, as_of)
            if px is not None and px > 0:
                marks[ticker] = px

    if hist_resolver is not None:
        consolidations = _consolidation_events(trades)
        for ticker in held:
            px = hist_resolver.mark(ticker, fund_currency, as_of)
            if px is not None and px > 0:
                scale = _pre_consolidation_scale(consolidations, ticker, as_of)
                marks.setdefault(ticker, px / scale)

    for ticker in held:
        snap = _snapshot_on_or_before(snapshots, ticker, as_of)
        if snap is not None and snap > 0:
            marks.setdefault(ticker, _in_fund_currency(snap, fund_currency))

    for ticker, price in last_trade_price.items():
        if ticker in held:
            marks.setdefault(ticker, price)

    if as_of >= date.today() and today_marks:
        for ticker in held:
            if ticker in today_marks:
                marks[ticker] = _in_fund_currency(today_marks[ticker], fund_currency)

    return marks


def _positions_at(trades, as_of: date) -> tuple[set[str], dict[str, float]]:
    shares: dict[str, float] = {}
    last_px: dict[str, float] = {}
    for tr in trades:
        if tr.trade_date > as_of:
            continue
        tk = tr.ticker.upper()
        last_px[tk] = tr.price
        delta = tr.shares if tr.side.upper().startswith("B") else -tr.shares
        shares[tk] = shares.get(tk, 0.0) + delta
    held = {k for k, v in shares.items() if v > 1e-9}
    return held, {k: last_px[k] for k in held if k in last_px}


def _ledger_points_for_fund(
    fund: TradeFund,
    trades,
    cash_events,
    reported,
    dates: set[date],
    *,
    from_transactions: bool,
    hist_resolver,
    snapshots,
    today_marks: dict[str, float] | None,
    fund_marks: dict[str, tuple[tuple[date, ...], tuple[float, ...]]] | None = None,
) -> list[BalancePoint]:
    sorted_dates = sorted(dates)
    points: list[BalancePoint] = []
    running_invested = 0.0

    for d in sorted_dates:
        held, last_trade_price = _positions_at(trades, d)
        events = [e for e in cash_events if e.event_date <= d]
        # Always prefer the broker-reported cash correction when one exists for
        # this date, in both trends modes. opening_cash is reconciled (see
        # fund_cash_reconcile.reconcile_opening_cash_to_target) specifically to
        # be correct *together with* this override — skipping it here (as the
        # "transactions" mode used to) replays raw trade cash flows against an
        # opening_cash value that was never meant to stand alone, wildly
        # overstating cash/NAV for any fund that has ever been reconciled.
        reported_cash = None
        if reported is not None and reported.balance_date <= d:
            reported_cash = reported.cash_balance

        marks = _marks_as_of(
            trades,
            d,
            fund.currency,
            held=held,
            last_trade_price=last_trade_price,
            hist_resolver=hist_resolver if from_transactions else None,
            snapshots=snapshots,
            today_marks=today_marks,
            fund_marks=fund_marks,
        )
        try:
            result = build_fund_ledger(
                fund,
                [t for t in trades if t.trade_date <= d],
                marks,
                d,
                cash_events=events,
                reported_cash=reported_cash,
                strict=False,
            )
        except Exception:
            continue
        raw = cumulative_cash_invested(fund, trades, cash_events, d)
        running_invested = max(running_invested, raw)
        points.append(
            BalancePoint(
                as_of=d,
                nav=result.nav,
                cash=result.cash_balance,
                positions=result.positions_value,
                source="ledger",
                invested=running_invested,
            )
        )
    return points


def collect_fund_balance_series(
    db_path,
    *,
    owner: str | None = None,
    fund_name: str | None = None,
    location: str = "All",
    trends_mode: TrendsMode = "statements",
    from_transactions: bool | None = None,
    use_cache: bool = True,
) -> list[FundBalanceSeries]:
    """
    Build fund value (NAV) time series.

    trends_mode:
      - statements: monthly Fidelity/broker statement snapshots (preferred)
      - transactions: month-end replay from share trades + historical prices
      - manual: statement snapshots + manual balance overrides
    """
    if from_transactions is not None:
        trends_mode = "transactions" if from_transactions else "manual"

    cache_key = _series_cache_key(db_path, owner, fund_name, location, trends_mode)
    now = time.time()
    if use_cache and cache_key in _series_cache:
        ts, cached = _series_cache[cache_key]
        if now - ts < _SERIES_CACHE_TTL_SEC:
            return cached

    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        funds = list_funds(conn)
        manual = fetch_fund_manual_balances(conn)

        if owner and owner.strip().lower() != "all":
            funds = [f for f in funds if f.owner == owner.strip()]
        if fund_name and fund_name.strip().lower() != "all":
            funds = [f for f in funds if f.name == fund_name.strip()]
        funds = [f for f in funds if matches_location(f, location)]

        fund_trades = {fund.id: fetch_trades_for_fund(conn, fund.id) for fund in funds}
        fund_cash = {fund.id: fetch_fund_cash_events(conn, fund.id) for fund in funds}
        fund_reported = {fund.id: fetch_fund_reported_cash(conn, fund.id) for fund in funds}
        fund_ticker_marks = {fund.id: fetch_fund_ticker_marks(conn, fund.id) for fund in funds}

        hist_resolver = None
        if funds:
            from ledger.quotes import HistoricalMarkResolver

            symbols: set[tuple[str, str]] = set()
            min_date: date | None = None
            for fund in funds:
                for tr in fund_trades[fund.id]:
                    symbols.add((tr.ticker, fund.currency))
                    min_date = (
                        tr.trade_date if min_date is None else min(min_date, tr.trade_date)
                    )
            if min_date is not None and symbols:
                hist_resolver = HistoricalMarkResolver.build(list(symbols), min_date)

        owners = {fund.owner for fund in funds}
        snapshots_by_owner = {o: _load_owner_snapshots(conn, o) for o in owners}
        today_marks_by_owner = {
            o: fetch_mark_prices_for_owner(conn, o) for o in owners
        }

        series: list[FundBalanceSeries] = []
        for fund in funds:
            trades = fund_trades[fund.id]
            points: list[BalancePoint] = []

            if trends_mode in ("statements", "manual"):
                points.extend(_statement_points(conn, fund.id))

            if trends_mode == "manual":
                m = manual.get(fund.id)
                if m:
                    points.append(
                        BalancePoint(
                            as_of=m.balance_date,
                            nav=m.nav,
                            cash=m.cash_balance or 0.0,
                            positions=m.positions_value or (m.nav - (m.cash_balance or 0)),
                            source="manual",
                        )
                    )

            if trades:
                start = min(t.trade_date for t in trades)
                ledger_dates = set(_month_end_dates(start, date.today()))
                ledger_dates |= {pt.as_of for pt in points}
                points.extend(
                    _ledger_points_for_fund(
                        fund,
                        trades,
                        fund_cash[fund.id],
                        fund_reported[fund.id],
                        ledger_dates,
                        from_transactions=True,
                        hist_resolver=hist_resolver,
                        snapshots=snapshots_by_owner[fund.owner],
                        today_marks=today_marks_by_owner[fund.owner],
                        fund_marks=fund_ticker_marks[fund.id] or None,
                    )
                )

            if not points:
                continue

            priority = {"manual": 4, "statement": 3, "ledger": 2, "snapshot": 2}
            by_date: dict[date, BalancePoint] = {}
            for pt in points:
                prev = by_date.get(pt.as_of)
                if prev is None:
                    by_date[pt.as_of] = pt
                elif _is_cash_only_statement_point(prev) and pt.source == "ledger":
                    by_date[pt.as_of] = pt
                elif priority.get(pt.source, 0) >= priority.get(prev.source, 0):
                    by_date[pt.as_of] = pt
            ordered = _with_invested(
                fund,
                trades,
                fund_cash[fund.id],
                list(by_date.values()),
            )
            ordered = tuple(sorted(ordered, key=lambda p: p.as_of))
            if len(ordered) >= 1:
                series.append(
                    FundBalanceSeries(
                        fund_name=fund.name,
                        owner=fund.owner,
                        currency=fund.currency,
                        location=currency_to_location(fund.currency),
                        points=ordered,
                    )
                )
        _series_cache[cache_key] = (now, series)
        return series
    finally:
        conn.close()


# Backwards-compatible alias
collect_fund_value_series = collect_fund_balance_series
