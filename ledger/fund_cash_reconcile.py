"""Align fund opening cash with broker / Fund List cash balances."""

from __future__ import annotations

import sqlite3
from datetime import date

from ledger.quotes import marks_for_fund
from ledger.trade_ledger import build_fund_ledger, build_fund_ledger_with_db
from ledger.trades_db import (
    TradeFund,
    fetch_fund_manual_balances,
    get_fund_by_name,
    init_trade_schema,
    list_funds,
    upsert_fund_manual_balance,
    upsert_fund_performance_snapshot,
)


def _latest_performance_snapshot_cash(conn: sqlite3.Connection, fund_id: int) -> float | None:
    row = conn.execute(
        """
        SELECT cash_balance
        FROM fund_performance_snapshots
        WHERE fund_id = ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        (fund_id,),
    ).fetchone()
    if row is None:
        return None
    return float(row[0])


def _fund_for_sheet_row(
    row,
    funds_by_name: dict[str, TradeFund],
    funds_by_owner: dict[str, list[TradeFund]],
) -> TradeFund | None:
    """Match a Fund List row to a trade_funds record."""
    fund = funds_by_name.get(row.fund_name)
    if fund is not None:
        return fund
    owner = str(row.owner).strip()
    owner_funds = funds_by_owner.get(owner, [])
    if len(owner_funds) == 1:
        return owner_funds[0]
    return None


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


def sync_opening_cash_from_fund_list(conn: sqlite3.Connection) -> dict[str, float]:
    """Reconcile opening cash from Fund List cash balances and HL/Aussuper snapshots."""
    from importers.funds import load_fund_lines_from_sheet

    init_trade_schema(conn)
    lines = load_fund_lines_from_sheet(date.today())
    updated: dict[str, float] = {}
    funds = list_funds(conn)
    funds_by_name = {f.name: f for f in funds}
    funds_by_owner: dict[str, list[TradeFund]] = {}
    for fund in funds:
        funds_by_owner.setdefault(fund.owner.strip(), []).append(fund)
    manual = fetch_fund_manual_balances(conn)

    targets: dict[str, float] = {}

    for owner_rows in lines.values():
        for row in owner_rows:
            fund = _fund_for_sheet_row(row, funds_by_name, funds_by_owner)
            if fund is None:
                continue
            m = manual.get(fund.id)
            if m is not None and m.cash_balance is not None:
                targets[fund.name] = float(m.cash_balance)
                continue
            if row.current_cash_balance is not None:
                targets[fund.name] = float(row.current_cash_balance)
                continue
            snap_cash = _latest_performance_snapshot_cash(conn, fund.id)
            if snap_cash is not None:
                targets[fund.name] = snap_cash

    for fund_name, target_cash in targets.items():
        new_opening = reconcile_opening_cash_to_target(conn, fund_name, target_cash)
        if new_opening is not None:
            updated[fund_name] = new_opening
    return updated


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
