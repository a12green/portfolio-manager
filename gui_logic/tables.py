"""Filtering, sorting and cell formatting for the GUI's tables. Pure functions, no Tk."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from gui_logic.formatting import fmt_money, fmt_pct, fmt_fund_money, pl_row_tag, tree_row_tags


def _market_match(market: str, market_sel: str) -> bool:
    from reports.portfolio_data import _matches_port_market

    return _matches_port_market(market, market_sel)


_FUND_SORT_KEYS: dict[str, Callable[[Any], Any]] = {
    "owner": lambda r: r.owner,
    "fund": lambda r: r.fund,
    "ccy": lambda r: r.native_currency,
    "ticker": lambda r: r.ticker,
    "shares": lambda r: r.shares,
    "avg": lambda r: r.avg_cost,
    "mark": lambda r: r.mark_price or 0.0,
    "value": lambda r: r.market_value or 0.0,
    "unreal": lambda r: r.unrealized_pl or 0.0,
    "pct": lambda r: r.pct_of_fund or 0.0,
}


def filter_fund_holdings(rows: Sequence[Any], market_sel: str, ticker_sel: str) -> list[Any]:
    """Keep rows in the chosen market whose ticker contains the typed text."""
    ticker = ticker_sel.strip().upper()
    return [
        r
        for r in rows
        if _market_match(r.market, market_sel) and (not ticker or ticker in r.ticker)
    ]


def sort_fund_holdings(rows: Sequence[Any], sort_col: str | None, sort_desc: bool) -> list[Any]:
    if not sort_col:
        return list(rows)
    if sort_col not in _FUND_SORT_KEYS:
        raise KeyError(f"Cannot sort fund holdings by {sort_col!r}")
    return sorted(rows, key=_FUND_SORT_KEYS[sort_col], reverse=sort_desc)


def fund_holding_cells(r: Any) -> tuple[str, ...]:
    return (
        r.owner,
        r.fund,
        r.native_currency,
        r.ticker,
        f"{r.shares:g}",
        fmt_money(r.avg_cost),
        fmt_money(r.mark_price) if r.mark_price is not None else "—",
        fmt_money(r.market_value) if r.market_value is not None else "—",
        fmt_money(r.unrealized_pl) if r.unrealized_pl is not None else "—",
        fmt_pct(r.pct_of_fund) if r.pct_of_fund is not None else "—",
    )


def fund_holding_rows(
    rows: Sequence[Any], market_sel: str, ticker_sel: str, sort_col: str | None, sort_desc: bool
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """(tags, cells) for each visible fund holding, in display order."""
    shown = sort_fund_holdings(filter_fund_holdings(rows, market_sel, ticker_sel), sort_col, sort_desc)
    return [
        (tree_row_tags(i, pl_row_tag(r.unrealized_pl)), fund_holding_cells(r))
        for i, r in enumerate(shown)
    ]


def fund_holdings_summary(rows: Sequence[Any], label: str) -> str:
    n_funds = len({(r.owner, r.fund) for r in rows})
    return (
        f"{label}: {len(rows)} holdings across {n_funds} fund(s) · all prices in USD "
        "(Fund Ccy shows the fund's own reporting currency) · "
        "click a column header to sort · same ticker in multiple funds shows as separate rows."
    )


def net_worth_cells(line: Any) -> tuple[str, ...]:
    return (
        line.label,
        line.kind,
        line.currency,
        fmt_fund_money(line.amount_local, line.currency),
        fmt_fund_money(line.amount_usd, "USD"),
        line.as_of,
        line.source,
    )


def net_worth_summary(fx_rates: dict[str, float], item_count: int, owner_label: str, use_live: bool) -> str:
    fx_note = " · ".join(
        f"{cur} {rate:.4f}" for cur, rate in sorted(fx_rates.items()) if cur not in ("USD", "US")
    )
    mode = "live" if use_live else "cached"
    return (
        f"{item_count} item(s) for {owner_label} · FX ({mode}): {fx_note or 'USD only'} · "
        "funds use the manual balance when set, otherwise the live ledger total"
    )


def net_worth_total_text(total_usd: float, owner_label: str) -> str:
    return f"Total ({owner_label}): {fmt_fund_money(total_usd, 'USD')}"


def balance_row_cells(
    *,
    name: str,
    owner: str,
    currency: str,
    manual_nav: float | None,
    manual_cash: float | None,
    manual_positions: float | None,
    manual_date: str | None,
    ledger_nav: float | None,
    ledger_cash: float | None,
) -> tuple[str, ...]:
    """One row of the Fund balances table. Blank manual fields show a dash."""
    return (
        name,
        owner,
        currency,
        fmt_fund_money(manual_nav, currency),
        fmt_fund_money(manual_cash, currency),
        fmt_fund_money(manual_positions, currency),
        manual_date or "—",
        fmt_fund_money(ledger_nav, currency),
        fmt_fund_money(ledger_cash, currency),
    )


def balances_summary(fund_count: int, manual_count: int) -> str:
    return (
        f"{fund_count} fund(s) · {manual_count} manual balance(s) on file · "
        "ledger totals from trades + stored mark prices"
    )


def trade_row_cells(t: Any) -> tuple[str, ...]:
    """One row of the Trades table. Options show their OCC or readable label."""
    from ledger.option_symbols import display_option_label

    label = display_option_label(occ_symbol=t.ticker) if t.is_option else t.ticker
    return (
        str(t.id),
        t.trade_date.isoformat(),
        t.instrument_type,
        t.side.upper(),
        label,
        f"{t.shares:g}",
        f"{t.price:.2f}",
        f"{t.fees:.2f}",
    )


def ledger_position_cells(pos: Any) -> tuple[str, ...]:
    """One row of the Ledger table, in the position's own currency."""
    from gui_logic.formatting import currency_loc

    cur = currency_loc(pos.currency)
    return (
        pos.ticker,
        f"{pos.shares:g}",
        fmt_money(pos.avg_cost, cur),
        fmt_money(pos.mark_price, cur) if pos.mark_price is not None else "—",
        fmt_money(pos.market_value, cur) if pos.market_value is not None else "—",
        fmt_money(pos.unrealized_pl, cur) if pos.unrealized_pl is not None else "—",
        fmt_money(pos.realized_pl, cur) if pos.realized_pl is not None else "—",
    )


def fund_summary_text(fund: Any, live_note: str = "") -> str:
    """One fund's cash, NAV and ROI, formatted in the fund's currency."""
    from gui_logic.formatting import currency_loc, fmt_pct

    cur = currency_loc(fund.currency)
    roi = fmt_pct(fund.roi_pct) if fund.roi_pct is not None else "—"
    return (
        f"Fund “{fund.name}”: cash {fmt_money(fund.cash, cur)} · "
        f"NAV {fmt_money(fund.nav, cur)} · ROI {roi}{live_note}"
    )


def fmt_volume(vol: int | None) -> str:
    if vol is None:
        return "—"
    if vol >= 1_000_000_000:
        return f"{vol / 1_000_000_000:.2f}B"
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.2f}M"
    if vol >= 1_000:
        return f"{vol / 1_000:.1f}K"
    return f"{vol:,}"


def mover_row_cells(row: Any, loc: str) -> tuple[str, ...]:
    """One row of a movers table. ``loc`` is the currency location code ("US" or "AUS")."""
    from gui_logic.formatting import fmt_money, fmt_pct

    return (
        str(row.rank),
        row.symbol,
        row.name,
        fmt_money(row.price, loc) if row.price is not None else "—",
        fmt_money(row.change, loc) if row.change is not None else "—",
        fmt_pct(row.change_pct) if row.change_pct is not None else "—",
        fmt_volume(row.volume),
    )
