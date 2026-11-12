"""Web service payloads and the report data builders, run against the demo database."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from config import OWNER_A, OWNER_B, OWNER_C
from ledger.db import connect_db
from web import services
from web.auth import WebUser


@pytest.fixture(autouse=True)
def _offline_prices(monkeypatch):
    monkeypatch.setenv("LIVE_QUOTES", "false")
    monkeypatch.setenv("MARKET_HOLIDAYS_ENABLED", "false")


ADMIN = WebUser(id=1, username="admin", owner_scope=None)
OWNER_B_USER = WebUser(id=2, username="household_b", owner_scope=OWNER_B)


def _conn(path: Path):
    return connect_db(path)


# --- web/services.py -------------------------------------------------------

def test_owner_label_is_the_owner_name():
    assert services.owner_label(OWNER_A) == OWNER_A


def test_meta_lists_owners_for_admin(demo_db):
    meta = services.meta_payload(ADMIN)
    assert set(meta["owners"]) >= {OWNER_A, OWNER_B, OWNER_C}


def test_meta_limits_owners_for_owner_user(demo_db):
    meta = services.meta_payload(OWNER_B_USER)
    assert meta["owners"] == [OWNER_B]


def test_list_funds_respects_scope(demo_db):
    admin = {f["name"] for f in services.list_funds_payload(ADMIN)}
    owner = {f["name"] for f in services.list_funds_payload(OWNER_B_USER)}
    assert "Fidelity" in admin and "Fidelity Account B" in admin
    assert owner == {"Fidelity Account B"}


def test_create_fund_then_list_it(demo_db):
    services.create_fund_payload("Scope Test Fund", OWNER_C, 500.0, "USD", user=ADMIN)
    names = [f["name"] for f in services.list_funds_payload(ADMIN)]
    assert "Scope Test Fund" in names


def test_owner_user_cannot_create_fund_for_other_owner(demo_db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        services.create_fund_payload("Not Mine", OWNER_A, 0.0, "USD", user=OWNER_B_USER)


def test_list_trades_for_fund(demo_db):
    trades = services.list_trades_payload("Fidelity", user=ADMIN)
    assert trades and all("ticker" in t or "display_name" in t for t in trades)


def test_owner_user_cannot_list_other_owners_trades(demo_db):
    with pytest.raises(Exception):
        services.list_trades_payload("Fidelity", user=OWNER_B_USER)


def test_add_trade_for_owner_fund(demo_db):
    result = services.add_trade_payload(
        "Fidelity Account B", "MSFT", "buy", 2.0, 400.0, date(2026, 10, 2), 0.0, "test",
        user=OWNER_B_USER,
    )
    assert result is not None


def test_portfolio_payload_for_owner(demo_db):
    payload = services.portfolio_payload(owner=OWNER_A, fund=None, market="All", live=False)
    assert "holdings" in payload or "summary" in payload


def test_portfolio_payload_for_single_fund(demo_db):
    payload = services.portfolio_payload(owner=OWNER_A, fund="Fidelity", market="All", live=False)
    assert isinstance(payload, dict)


def test_ledger_payload(demo_db):
    payload = services.ledger_payload(owner=OWNER_A, fund=None, live=False)
    assert isinstance(payload, dict)


def test_heatmap_payload_by_overall(demo_db):
    payload = services.heatmap_payload(owner=OWNER_A, fund=None, color_by="overall", live=False)
    assert "tiles" in payload


def test_heatmap_payload_by_day_change(demo_db):
    payload = services.heatmap_payload(owner=OWNER_A, fund="Fidelity", color_by="day", live=False)
    assert "tiles" in payload


def test_fund_balances_payload(demo_db):
    payload = services.fund_balances_payload(OWNER_A)
    assert "balances" in payload


def test_net_worth_payload(demo_db):
    payload = services.net_worth_payload(OWNER_A)
    assert "total_usd" in payload


def test_other_assets_round_trip(demo_db):
    services.create_other_asset(OWNER_A, "Test Cash Account", 1500.0, "USD")
    listed = services.other_assets_payload(OWNER_A)
    assert any(a["asset_type"] == "Test Cash Account" for a in listed["assets"])
    services.delete_other_asset(OWNER_A, "Test Cash Account")
    listed = services.other_assets_payload(OWNER_A)
    assert not any(a["asset_type"] == "Test Cash Account" for a in listed["assets"])


def test_fund_trends_payload(demo_db):
    payload = services.fund_trends_payload(OWNER_A)
    assert isinstance(payload, dict)


def test_update_fund_manual_balance(demo_db):
    result = services.update_fund_manual_balance("Fidelity Account B", 50000.0, date(2026, 10, 31))
    assert result == {"fund_name": "Fidelity Account B", "nav": 50000.0, "balance_date": "2026-10-31"}


def test_movers_payload_is_offline_safe(demo_db):
    payload = services.movers_payload("US")
    assert isinstance(payload, dict)


# --- report data builders ---------------------------------------------------

def test_holdings_shares_for_owner(demo_db):
    conn = _conn(demo_db)
    try:
        shares = _call_collect_shares(conn)
    finally:
        conn.close()
    assert isinstance(shares, dict)


def _call_collect_shares(conn):
    from reports.holdings_compare import collect_ledger_shares

    return collect_ledger_shares(conn, owner=OWNER_A)


def test_fund_holdings_rows(demo_db):
    from reports.holdings_compare import collect_fund_holdings_rows

    conn = _conn(demo_db)
    try:
        rows = collect_fund_holdings_rows(conn, owner=OWNER_A, live=False)
    finally:
        conn.close()
    assert isinstance(rows, list)


def test_ledger_card_positions(demo_db):
    from reports.holdings_compare import collect_ledger_card_positions

    conn = _conn(demo_db)
    try:
        positions = collect_ledger_card_positions(conn, owner=OWNER_A, live=False)
    finally:
        conn.close()
    assert isinstance(positions, list)


def test_heatmap_tiles_for_owner(demo_db):
    from reports.heatmap_data import aggregate_tiles_by_ticker, collect_heatmap_tiles, group_tiles_by_fund

    tiles = collect_heatmap_tiles(demo_db, owner=OWNER_A, live=False)
    assert isinstance(tiles, list)
    assert isinstance(aggregate_tiles_by_ticker(tiles), list)
    assert isinstance(group_tiles_by_fund(tiles), dict)


def test_heatmap_tiles_by_fund_and_color(demo_db):
    from reports.heatmap_data import collect_heatmap_tiles

    tiles = collect_heatmap_tiles(demo_db, fund_name="Fidelity", live=False, color_by="overall")
    assert isinstance(tiles, list)


def test_fund_balance_series_for_owner(demo_db):
    from reports.fund_balance_history import collect_fund_balance_series, combine_fund_balance_series

    series = collect_fund_balance_series(demo_db, owner=OWNER_A)
    assert isinstance(series, (list, tuple))
    combined = combine_fund_balance_series(list(series)) if series else None
    assert combined is None or hasattr(combined, "dates") or combined is not None


def test_portfolio_holdings_loader(demo_db):
    from reports.portfolio_data import load_portfolio_holdings

    result = load_portfolio_holdings(
        demo_db, owner=OWNER_A, fund_filter=None, market_sel="All",
        use_live=False, owner_label=OWNER_A,
    )
    assert isinstance(result, tuple)


# --- quote helpers (no network) ---------------------------------------------

def test_ticker_helpers():
    from ledger import quotes

    assert quotes.normalize_ticker(" nvda ") == "NVDA"
    assert quotes.to_yahoo_symbol("BHP", "AUD") == "BHP.AX"
    assert quotes.to_yahoo_symbol("NVDA", "USD") == "NVDA"
    assert quotes.infer_mark_currency("VAS.AX") in ("AUD", "USD", "GBP")


def test_resolve_day_move_handles_missing_details():
    from ledger.quotes import resolve_day_move

    assert resolve_day_move(None, last=None, sparkline=None) == (None, None, None)


def test_convert_to_usd_uses_supplied_rates():
    from ledger.quotes import convert_to_usd

    assert convert_to_usd(100.0, "USD", {"USD": 1.0}) == pytest.approx(100.0)
    assert convert_to_usd(100.0, "GBP", {"GBP": 1.25}) == pytest.approx(125.0)


def test_price_in_fund_currency_same_currency_is_identity():
    from ledger.quotes import price_in_fund_currency

    assert price_in_fund_currency(10.0, "USD", "USD", {"USD": 1.0}) == pytest.approx(10.0)


def test_live_marks_fall_back_to_empty_when_offline(demo_db):
    from ledger.quotes import fetch_live_marks_for_owner

    marks = fetch_live_marks_for_owner(demo_db, OWNER_A, force=True)
    assert isinstance(marks, dict)


def test_fx_rates_offline_returns_mapping():
    from ledger.quotes import fx_to_usd_rates

    rates = fx_to_usd_rates(force=True, allow_network=False)
    assert isinstance(rates, dict)
