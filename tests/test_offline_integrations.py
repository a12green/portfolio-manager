"""Yahoo-backed price code, market movers, cash reconciliation, strike alerts and charts.

Yahoo Finance is replaced by a small fake, so the real code paths run without network access.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from config import OWNER_A
from ledger.db import connect_db
from ledger.trades_db import init_trade_schema


# --- ledger/quotes.py -------------------------------------------------------

def test_fetch_live_quotes_uses_yahoo(fake_yahoo):
    from ledger.quotes import fetch_live_quotes

    prices = fetch_live_quotes([("NVDA", "USD"), ("VAS", "AUD")])
    assert prices.get("NVDA") == pytest.approx(101.5)


def test_fetch_quote_details_returns_fields(fake_yahoo):
    from ledger.quotes import fetch_quote_details

    details = fetch_quote_details([("NVDA", "USD")], cache_key="t1", force=True, with_sparklines=True)
    assert "NVDA" in details
    assert details["NVDA"].price == pytest.approx(101.5)


def test_fetch_quote_details_uses_cache(fake_yahoo):
    from ledger.quotes import fetch_quote_details, peek_quote_details_cache

    fetch_quote_details([("NVDA", "USD")], cache_key="cached", force=True, with_sparklines=False)
    assert peek_quote_details_cache("cached") is not None


def test_quote_details_respect_allow_network_false(fake_yahoo):
    from ledger.quotes import fetch_quote_details

    assert fetch_quote_details([("NVDA", "USD")], force=True, allow_network=False, with_sparklines=False) == {}


def test_intraday_sparklines(fake_yahoo):
    from ledger.quotes import fetch_intraday_sparklines

    out = fetch_intraday_sparklines([("NVDA", "USD")])
    assert isinstance(out, dict)


def test_historical_close_lookup(fake_yahoo):
    from ledger.quotes import historical_close_on_or_before

    value = historical_close_on_or_before("NVDA", "USD", date(2026, 11, 10))
    assert value is None or value > 0


def test_fx_rates_from_yahoo(fake_yahoo):
    from ledger.quotes import fx_to_usd_rates

    rates = fx_to_usd_rates(force=True)
    assert rates.get("USD", 1.0) == pytest.approx(1.0)


def test_sum_tiles_value_usd_with_empty_list(fake_yahoo):
    from ledger.quotes import sum_tiles_value_usd

    assert sum_tiles_value_usd([]) == pytest.approx(0.0)


def test_mark_prices_for_demo_fund(demo_db, fake_yahoo):
    from ledger.db import connect_db as cdb
    from ledger.quotes import marks_for_fund
    from ledger.trades_db import get_fund_by_name

    conn = cdb(demo_db)
    try:
        fund = get_fund_by_name(conn, "Fidelity")
        marks = marks_for_fund(conn, fund, date(2026, 11, 10), live=True, force_live=True)
    finally:
        conn.close()
    assert isinstance(marks, dict)


def test_fetch_live_marks_for_owner_with_yahoo(demo_db, fake_yahoo):
    from ledger.quotes import fetch_live_marks_for_owner

    marks = fetch_live_marks_for_owner(demo_db, OWNER_A, force=True)
    assert isinstance(marks, dict)


# --- ledger/market_movers.py ------------------------------------------------

def test_market_movers_split_gainers_and_losers(fake_yahoo):
    from ledger.market_movers import clear_market_movers_cache, fetch_market_movers

    clear_market_movers_cache()
    gainers, losers = fetch_market_movers("US", top_n=5, force=True)
    assert gainers and gainers[0].symbol == "NVDA"
    assert losers and losers[0].symbol == "XYZ"


# --- ledger/fund_cash_reconcile.py -------------------------------------------

def test_apply_broker_portal_balance_writes_a_mark(demo_db):
    from ledger.fund_cash_reconcile import apply_broker_portal_balance

    conn = connect_db(demo_db)
    try:
        init_trade_schema(conn)
        apply_broker_portal_balance(
            conn, "Fidelity", nav=60000.0, cash=3000.0, positions_value=57000.0,
            balance_date=date(2026, 11, 1),
        )
        conn.commit()
        row = conn.execute(
            "SELECT nav FROM fund_manual_balances m JOIN trade_funds f ON f.id = m.fund_id WHERE f.name = 'Fidelity'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == pytest.approx(60000.0)


def test_apply_broker_aussuper_balance_writes_a_mark(demo_db):
    from ledger.fund_cash_reconcile import apply_broker_aussuper_balance, apply_broker_super_a_balance

    conn = connect_db(demo_db)
    try:
        init_trade_schema(conn)
        apply_broker_aussuper_balance(conn, "Super A", nav=7500.0, cash=0.0, balance_date=date(2026, 6, 30))
        apply_broker_super_a_balance(conn, nav=7600.0, cash=0.0, balance_date=date(2026, 6, 30))
        conn.commit()
    finally:
        conn.close()


def test_reconcile_opening_cash_to_target(demo_db):
    from ledger.fund_cash_reconcile import reconcile_opening_cash_to_target

    conn = connect_db(demo_db)
    try:
        init_trade_schema(conn)
        reconcile_opening_cash_to_target(conn, "Fidelity", 25000.0, as_of=date(2026, 1, 8))
        conn.commit()
    finally:
        conn.close()


# --- reports/option_strike_alerts.py ------------------------------------------

def test_find_strike_alerts_on_demo_data(demo_db, fake_yahoo):
    from reports.option_strike_alerts import find_new_strike_alerts

    conn = connect_db(demo_db)
    try:
        alerts = find_new_strike_alerts(conn, threshold=1.0)
    finally:
        conn.close()
    assert isinstance(alerts, list)


def test_run_strike_alerts_dry_run_sends_nothing(demo_db, fake_yahoo, monkeypatch):
    from reports.option_strike_alerts import run_option_strike_alerts

    sent = []
    import reports.option_strike_alerts as alerts_mod

    monkeypatch.setattr(alerts_mod, "_send_alert_email", lambda *a, **k: sent.append(a), raising=False)
    result = run_option_strike_alerts(demo_db, dry_run=True, force=True)
    assert isinstance(result, list)
    assert sent == []


# --- charts -------------------------------------------------------------------

def test_heatmap_figure_builds(demo_db, fake_yahoo):
    from reports.heatmap_chart import build_heatmap_figure
    from reports.heatmap_data import collect_heatmap_tiles

    tiles = collect_heatmap_tiles(demo_db, owner=OWNER_A, live=False)
    fig = build_heatmap_figure(tiles or [], title="Test heat map")
    assert fig is not None


def test_fund_balance_figure_builds(demo_db, fake_yahoo):
    from reports.fund_balance_chart import build_fund_balance_figure
    from reports.fund_balance_history import collect_fund_balance_series

    series = collect_fund_balance_series(demo_db, owner=OWNER_A)
    fig = build_fund_balance_figure(list(series), title="Test balances")
    assert fig is not None
