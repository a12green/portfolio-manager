"""Database snapshot writers and readers, and the web error paths that were not yet exercised."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from config import OWNER_A, OWNER_B
from ledger.db import (
    FundWeeklyRow,
    StockSnapshotRow,
    connect_db,
    fetch_fund_owner_weekly_totals,
    fetch_fund_weekly,
    fetch_latest_fund_lines,
    fetch_latest_stock_holdings,
    fetch_latest_two_fund_weekly,
    fetch_latest_two_stock_totals,
    fetch_stock_totals_by_owner,
    init_schema,
    replace_fund_weekly_snapshots,
    upsert_fund_weekly_snapshot,
    upsert_stock_daily_snapshots,
)


def _stock(day: date, owner: str, ticker: str, shares: float, last: float) -> StockSnapshotRow:
    cost = shares * 100.0
    mkt = shares * last
    return StockSnapshotRow(
        snapshot_date=day,
        owner=owner,
        ticker=ticker,
        shares=shares,
        avg_cost=100.0,
        last_price=last,
        cost_basis=cost,
        mkt_value=mkt,
        pl_dollar=mkt - cost,
        pl_pct=(mkt - cost) / cost * 100.0,
    )


def _fund(week: date, owner: str, name: str, total: float, profit: float) -> FundWeeklyRow:
    return FundWeeklyRow(
        week_start=week,
        owner=owner,
        fund_name=name,
        cash_invested=total - profit,
        current_cash_balance=100.0,
        active_purchase_amount=0.0,
        fund_total_usd=total,
        total_profit_usd=profit,
    )


@pytest.fixture
def conn(db_path):
    c = connect_db(db_path)
    init_schema(c)
    yield c
    c.close()


def test_stock_snapshots_are_written_and_read_back(conn):
    rows = [
        _stock(date(2026, 10, 1), OWNER_A, "NVDA", 10, 150.0),
        _stock(date(2026, 10, 1), OWNER_A, "AAPL", 5, 200.0),
        _stock(date(2026, 10, 1), OWNER_B, "MSFT", 3, 420.0),
    ]
    assert upsert_stock_daily_snapshots(conn, rows) == 3
    conn.commit()
    totals = fetch_stock_totals_by_owner(conn)
    assert {t["owner"] for t in totals} == {OWNER_A, OWNER_B}
    holdings = fetch_latest_stock_holdings(conn, OWNER_A)
    assert {h["ticker"] for h in holdings} == {"NVDA", "AAPL"}


def test_latest_two_stock_totals_compare_days(conn):
    upsert_stock_daily_snapshots(conn, [_stock(date(2026, 10, 1), OWNER_A, "NVDA", 10, 150.0)])
    upsert_stock_daily_snapshots(conn, [_stock(date(2026, 10, 2), OWNER_A, "NVDA", 10, 160.0)])
    conn.commit()
    latest, previous = fetch_latest_two_stock_totals(conn, OWNER_A)
    assert latest is not None and previous is not None
    assert latest > previous


def test_fund_weekly_upsert_and_fetch(conn):
    week = date(2026, 11, 2)
    upsert_fund_weekly_snapshot(conn, _fund(week, OWNER_A, "Fidelity", 58000.0, 8000.0))
    conn.commit()
    rows = fetch_fund_weekly(conn)
    assert any(r["fund_name"] == "Fidelity" for r in rows)
    owner_totals = fetch_fund_owner_weekly_totals(conn)
    assert any(t["owner"] == OWNER_A for t in owner_totals)
    lines = fetch_latest_fund_lines(conn, OWNER_A)
    assert lines and lines[0]["fund_name"] == "Fidelity"


def test_fund_weekly_replace_for_one_owner_and_week(conn):
    week = date(2026, 11, 2)
    upsert_fund_weekly_snapshot(conn, _fund(week, OWNER_A, "Old Fund", 1000.0, 10.0))
    conn.commit()
    written = replace_fund_weekly_snapshots(conn, week, OWNER_A, [_fund(week, OWNER_A, "New Fund", 2000.0, 20.0)])
    conn.commit()
    assert written == 1
    names = {r["fund_name"] for r in fetch_latest_fund_lines(conn, OWNER_A)}
    assert "New Fund" in names and "Old Fund" not in names


def test_latest_two_fund_weeks_compare(conn):
    upsert_fund_weekly_snapshot(conn, _fund(date(2026, 10, 26), OWNER_A, "Fidelity", 50000.0, 5000.0))
    upsert_fund_weekly_snapshot(conn, _fund(date(2026, 11, 2), OWNER_A, "Fidelity", 58000.0, 8000.0))
    conn.commit()
    # Returns ((latest_total, previous_total), (latest_profit, previous_profit)).
    (latest_total, prev_total), (latest_profit, prev_profit) = fetch_latest_two_fund_weekly(conn, OWNER_A)
    assert latest_total == pytest.approx(58000.0)
    assert prev_total == pytest.approx(50000.0)
    assert latest_profit == pytest.approx(8000.0)


# --- web error paths ---------------------------------------------------------------------

@pytest.fixture
def web_client(demo_db, monkeypatch):
    import getpass
    from web import auth, set_password
    from web.app import app

    auth._LOGIN_ATTEMPTS.clear()
    answers = iter(["admin-passphrase-1", "admin-passphrase-1"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert set_password.main(["admin", "--create"]) == 0
    answers = iter(["household-b-pass-1", "household-b-pass-1"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert set_password.main(["household_b", "--create", "--owner", OWNER_B]) == 0
    with TestClient(app) as c:
        yield c


def _login(c, user, pw):
    assert c.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200


def test_profile_email_change_needs_current_password(web_client):
    _login(web_client, "admin", "admin-passphrase-1")
    r = web_client.put("/api/auth/profile", json={"email": "owner@example.com"},
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_profile_email_change_with_wrong_password_is_refused(web_client):
    _login(web_client, "admin", "admin-passphrase-1")
    r = web_client.put("/api/auth/profile",
                       json={"email": "owner@example.com", "current_password": "wrong-password-x"},
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_profile_rejects_invalid_email(web_client):
    _login(web_client, "admin", "admin-passphrase-1")
    r = web_client.put("/api/auth/profile",
                       json={"email": "not-an-email", "current_password": "admin-passphrase-1"},
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_owner_cannot_update_balance_of_another_owners_fund(web_client):
    _login(web_client, "household_b", "household-b-pass-1")
    r = web_client.put("/api/funds/Fidelity/balance", json={"nav": 1000.0},
                       headers={"Content-Type": "application/json"})
    assert r.status_code in (403, 404)


def test_balance_update_for_unknown_fund_is_404(web_client):
    _login(web_client, "admin", "admin-passphrase-1")
    r = web_client.put("/api/funds/No%20Such%20Fund/balance", json={"nav": 1000.0},
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 404


def test_balance_update_rejects_non_positive_nav(web_client):
    _login(web_client, "admin", "admin-passphrase-1")
    r = web_client.put("/api/funds/Fidelity/balance", json={"nav": 0},
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_internal_errors_do_not_leak_details(web_client, monkeypatch):
    from web import services

    _login(web_client, "admin", "admin-passphrase-1")

    def _boom(**kwargs):
        raise RuntimeError("database locked at /Users/example/secret/path.sqlite3")

    monkeypatch.setattr(services, "portfolio_payload", _boom)
    r = web_client.get("/api/portfolio", params={"owner": OWNER_A, "live": "false"})
    assert r.status_code == 500
    assert r.json()["detail"] == "Internal error"
    assert "secret" not in r.text


def test_owner_cannot_read_another_owners_portfolio(web_client):
    _login(web_client, "household_b", "household-b-pass-1")
    r = web_client.get("/api/portfolio", params={"owner": OWNER_A, "live": "false"})
    assert r.status_code in (403, 404)
