"""Align fund opening cash with broker / Fund List cash balances."""

from __future__ import annotations

import sqlite3
from datetime import date

from ledger.quotes import marks_for_fund
from ledger.trade_ledger import build_fund_ledger
from ledger.trades_db import (
    get_fund_by_name,
    init_trade_schema,
    upsert_fund_manual_balance,
    upsert_fund_performance_snapshot,
)


def reconcile_opening_cash_to_target(
    conn: sqlite3.Connection,
    fund_name: str,
    target_cash: float,
    *,
    as_of: date | None = None,
) -> float | None:
    """
    Set opening_cash so ledger cash matches target_cash (broker / Fund List).
    Returns new opening_cash, or None if fund not found.
    """
    init_trade_schema(conn)
    fund = get_fund_by_name(conn, fund_name.strip())
    if fund is None:
        return None
    as_of = as_of or date.today()
    marks = marks_for_fund(conn, fund, as_of, live=False)
    # Deliberately NOT build_fund_ledger_with_db: it applies any *existing*
    # fund_reported_cash override unconditionally. If this fund was already
    # synced once before (an older override on file), that override would
    # flatly overwrite the computed cash regardless of as_of, making
    # trade_derived reflect the old override minus opening_cash instead of
    # real trade activity -- garbage in this context, since this calculation
    # exists specifically to measure what trades+events alone imply, so a new
    # opening_cash can be solved for. Any prior override is precisely the
    # thing being recalibrated here and must be excluded from its own inputs.
    from ledger.trades_db import fetch_fund_cash_events, fetch_trades_for_fund

    trades = fetch_trades_for_fund(conn, fund.id)
    cash_events = fetch_fund_cash_events(conn, fund.id)
    result = build_fund_ledger(
        fund, trades, marks, as_of, cash_events=cash_events, reported_cash=None, strict=False
    )
    trade_derived = float(result.cash_balance) - float(fund.opening_cash)
    new_opening = float(target_cash) - trade_derived
    if abs(new_opening - fund.opening_cash) < 0.01:
        return fund.opening_cash
    conn.execute(
        "UPDATE trade_funds SET opening_cash = ? WHERE id = ?",
        (new_opening, fund.id),
    )
    conn.commit()
    return new_opening


def apply_broker_portal_balance(
    conn: sqlite3.Connection,
    fund_name: str,
    *,
    nav: float,
    cash: float,
    positions_value: float | None = None,
    balance_date: date | None = None,
    notes: str = "Broker portal",
) -> None:
    """Store broker-reported NAV/cash (manual balance + performance snapshot)."""
    balance_date = balance_date or date.today()
    positions = (
        float(positions_value)
        if positions_value is not None
        else float(nav) - float(cash)
    )
    fund = get_fund_by_name(conn, fund_name.strip())
    if fund is None:
        return
    reconcile_opening_cash_to_target(conn, fund_name, float(cash))
    upsert_fund_manual_balance(
        conn,
        fund.id,
        balance_date,
        float(nav),
        cash_balance=float(cash),
        positions_value=positions,
        notes=notes,
    )
    upsert_fund_performance_snapshot(
        conn,
        balance_date,
        fund.id,
        cash_balance=float(cash),
        positions_value=positions,
        nav=float(nav),
        invested_capital=0.0,
        cumulative_realized_pl=0.0,
        roi_pct=None,
    )


def apply_broker_aussuper_balance(
    conn: sqlite3.Connection,
    fund_name: str,
    *,
    nav: float,
    cash: float,
    balance_date: date | None = None,
) -> None:
    """Store Australian Super portal totals (pooled options + Member Direct)."""
    apply_broker_portal_balance(
        conn,
        fund_name,
        nav=nav,
        cash=cash,
        balance_date=balance_date,
        notes="Australian Super portal",
    )


def apply_broker_super_a_balance(
    conn: sqlite3.Connection,
    *,
    nav: float,
    cash: float,
    balance_date: date | None = None,
) -> None:
    apply_broker_aussuper_balance(conn, "Super A", nav=nav, cash=cash, balance_date=balance_date)
