"""Shared write operations: scope checks, validation and the stored result."""

from __future__ import annotations

from datetime import date

import pytest

from app.use_cases import (
    clear_fund_balance,
    create_fund,
    delete_other_asset,
    record_trade,
    save_fund_balance,
    save_other_asset,
)
from config import OWNER_A, OWNER_B
from ledger.db import connect_db
from ledger.trades_db import fetch_owner_other_assets, get_fund_by_name, init_trade_schema


def test_record_trade_stores_and_returns_the_trade(demo_db):
    t = record_trade(demo_db, fund_name="Fidelity", ticker=" aapl ", side="BUY", shares=2,
                     price=190.0, trade_date=date(2026, 10, 2), fees=1.0)
    assert t.ticker == "AAPL" and t.side == "buy" and t.shares == 2


def test_owner_scope_refuses_another_owners_fund(demo_db):
    with pytest.raises(PermissionError):
        record_trade(demo_db, fund_name="Fidelity", ticker="MSFT", side="buy", shares=1, price=1.0,
                     owner_scope=OWNER_B)


def test_owner_scope_allows_own_fund(demo_db):
    t = record_trade(demo_db, fund_name="Fidelity Account B", ticker="MSFT", side="buy", shares=1,
                     price=1.0, owner_scope=OWNER_B)
    assert t.ticker == "MSFT"


def test_unknown_fund_is_a_value_error(demo_db):
    with pytest.raises(ValueError, match="Unknown fund"):
        record_trade(demo_db, fund_name="Nope", ticker="X", side="buy", shares=1, price=1.0)


def test_create_fund_checks_owner_and_duplicates(demo_db):
    fund = create_fund(demo_db, name="New Fund", owner=OWNER_A, opening_cash=10.0, currency="usd")
    assert fund.currency == "USD"
    with pytest.raises(ValueError, match="already exists"):
        create_fund(demo_db, name="New Fund", owner=OWNER_A, opening_cash=0, currency="USD")
    with pytest.raises(ValueError, match="Unknown owner"):
        create_fund(demo_db, name="Other", owner="Nobody", opening_cash=0, currency="USD")


def test_create_fund_refuses_other_owner_when_scoped(demo_db):
    with pytest.raises(PermissionError):
        create_fund(demo_db, name="Sneaky", owner=OWNER_A, opening_cash=0, currency="USD", owner_scope=OWNER_B)


def test_fund_balance_saved_then_cleared(demo_db):
    save_fund_balance(demo_db, fund_name="Fidelity", as_of=date(2026, 10, 31), nav=61000.0, cash=3000.0)
    conn = connect_db(demo_db)
    try:
        init_trade_schema(conn)
        fund = get_fund_by_name(conn, "Fidelity")
        stored = conn.execute("SELECT nav FROM fund_manual_balances WHERE fund_id = ?", (fund.id,)).fetchone()
    finally:
        conn.close()
    assert stored[0] == pytest.approx(61000.0)
    assert clear_fund_balance(demo_db, fund_name="Fidelity") is True
    assert clear_fund_balance(demo_db, fund_name="Fidelity") is False


def test_fund_balance_respects_owner_scope(demo_db):
    with pytest.raises(PermissionError):
        save_fund_balance(demo_db, fund_name="Fidelity", as_of=date(2026, 10, 31), nav=1.0, owner_scope=OWNER_B)
    with pytest.raises(PermissionError):
        clear_fund_balance(demo_db, fund_name="Fidelity", owner_scope=OWNER_B)


def test_other_asset_saved_and_deleted(demo_db):
    save_other_asset(demo_db, owner=OWNER_A, asset_type="Term deposit", amount=2500.0, currency="AUD")
    conn = connect_db(demo_db)
    try:
        assert any(a.asset_type == "Term deposit" for a in fetch_owner_other_assets(conn, OWNER_A))
    finally:
        conn.close()
    assert delete_other_asset(demo_db, owner=OWNER_A, asset_type="Term deposit") is True
    assert delete_other_asset(demo_db, owner=OWNER_A, asset_type="Term deposit") is False


def test_other_asset_respects_owner_scope(demo_db):
    with pytest.raises(PermissionError):
        save_other_asset(demo_db, owner=OWNER_A, asset_type="x", amount=1.0, owner_scope=OWNER_B)
    with pytest.raises(PermissionError):
        delete_other_asset(demo_db, owner=OWNER_A, asset_type="x", owner_scope=OWNER_B)


from app.use_cases import fund_holdings, fund_trades  # noqa: E402


def test_fund_trades_are_oldest_first(demo_db):
    trades = fund_trades(demo_db, fund_name="Fidelity")
    dates = [t.trade_date for t in trades]
    assert dates == sorted(dates) and len(trades) == 6


def test_fund_trades_refuse_another_owner_and_unknown_fund(demo_db):
    with pytest.raises(PermissionError):
        fund_trades(demo_db, fund_name="Fidelity", owner_scope=OWNER_B)
    with pytest.raises(ValueError, match="Unknown fund"):
        fund_trades(demo_db, fund_name="Nope")


def test_fund_holdings_for_owner_lists_positions(demo_db, fake_yahoo):
    rows = fund_holdings(demo_db, owner=OWNER_A, live=False)
    assert {r.ticker for r in rows} >= {"NVDA", "MSFT"}
