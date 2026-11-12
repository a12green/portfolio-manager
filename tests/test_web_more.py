"""More web API endpoints, exercised through the HTTP client against the demo database."""

from __future__ import annotations

import getpass

import pytest
from fastapi.testclient import TestClient

from config import OWNER_A, OWNER_B
from web import auth, set_password
from web.app import app

ADMIN_PW = "admin-passphrase-1"
B_PW = "household-b-pass-1"
JSON = {"Content-Type": "application/json"}


def _set_password(monkeypatch, argv, password):
    answers = iter([password, password])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert set_password.main(argv) == 0


@pytest.fixture(autouse=True)
def _reset_limits():
    auth._LOGIN_ATTEMPTS.clear()
    auth._RESET_ATTEMPTS.clear()
    yield


@pytest.fixture
def client(demo_db, monkeypatch, fake_yahoo):
    _set_password(monkeypatch, ["admin", "--create"], ADMIN_PW)
    _set_password(monkeypatch, ["household_b", "--create", "--owner", OWNER_B], B_PW)
    with TestClient(app) as c:
        yield c


def _admin(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert r.status_code == 200


def _owner_b(client):
    r = client.post("/api/auth/login", json={"username": "household_b", "password": B_PW})
    assert r.status_code == 200


def test_profile_get_and_put(client):
    _admin(client)
    assert client.get("/api/auth/profile").status_code == 200
    r = client.put("/api/auth/profile", json={"display_name": "Admin User"}, headers=JSON)
    assert r.status_code in (200, 400, 422)


def test_portfolio_endpoint(client):
    _admin(client)
    r = client.get("/api/portfolio", params={"owner": OWNER_A, "market": "All", "live": "false"})
    assert r.status_code == 200


def test_portfolio_endpoint_for_fund(client):
    _admin(client)
    r = client.get("/api/portfolio", params={"owner": OWNER_A, "fund": "Fidelity", "live": "false"})
    assert r.status_code == 200


def test_ledger_endpoint(client):
    _admin(client)
    r = client.get("/api/ledger", params={"owner": OWNER_A, "live": "false"})
    assert r.status_code == 200


def test_heatmap_endpoint(client):
    _admin(client)
    r = client.get("/api/heatmap", params={"owner": OWNER_A, "color_by": "overall", "live": "false"})
    assert r.status_code == 200


def test_market_movers_endpoint(client):
    _admin(client)
    r = client.get("/api/market-movers", params={"market": "US"})
    assert r.status_code == 200


def test_fund_balances_endpoint(client):
    _admin(client)
    r = client.get("/api/fund-balances", params={"owner": OWNER_A})
    assert r.status_code == 200


def test_fund_trades_endpoint_for_owner(client):
    _owner_b(client)
    r = client.get("/api/funds/Fidelity%20Account%20B/trades")
    assert r.status_code == 200


def test_update_fund_balance_endpoint(client):
    _admin(client)
    r = client.put(
        "/api/funds/Fidelity/balance",
        json={"nav": 61000.0, "balance_date": "2026-10-31"},
        headers=JSON,
    )
    assert r.status_code == 200
    assert r.json()["nav"] == 61000.0


def test_net_worth_endpoint(client):
    _admin(client)
    r = client.get("/api/net-worth", params={"owner": OWNER_A})
    assert r.status_code == 200


def test_other_assets_create_list_delete(client):
    _admin(client)
    r = client.post("/api/other-assets", json={"asset_type": "Term deposit", "amount": 2500, "currency": "AUD"}, headers=JSON)
    assert r.status_code == 200
    r = client.get("/api/other-assets", params={"owner": OWNER_A})
    assert any(a["asset_type"] == "Term deposit" for a in r.json()["assets"])
    r = client.delete("/api/other-assets/Term%20deposit", params={"owner": OWNER_A}, headers=JSON)
    assert r.status_code == 200


def test_fund_trends_endpoint(client):
    _admin(client)
    r = client.get("/api/fund-trends", params={"owner": OWNER_A})
    assert r.status_code == 200


def test_add_trade_endpoint_for_owner_fund(client):
    _owner_b(client)
    r = client.post(
        "/api/trades",
        json={"fund_name": "Fidelity Account B", "ticker": "MSFT", "side": "buy", "shares": 2, "price": 400, "trade_date": "2026-10-05"},
        headers=JSON,
    )
    assert r.status_code == 200


def test_add_trade_rejects_unknown_side(client):
    _admin(client)
    r = client.post(
        "/api/trades",
        json={"fund_name": "Fidelity", "ticker": "MSFT", "side": "hold", "shares": 2, "price": 400},
        headers=JSON,
    )
    assert r.status_code in (400, 422)


def test_create_fund_endpoint_for_admin(client):
    _admin(client)
    r = client.post("/api/funds", json={"name": "Endpoint Fund", "owner": OWNER_A, "opening_cash": 100, "currency": "usd"}, headers=JSON)
    assert r.status_code == 200
    names = [f["name"] for f in client.get("/api/funds").json()["funds"]]
    assert "Endpoint Fund" in names


def test_create_fund_endpoint_refuses_other_owner(client):
    _owner_b(client)
    r = client.post("/api/funds", json={"name": "Sneaky", "owner": OWNER_A, "opening_cash": 0, "currency": "USD"}, headers=JSON)
    assert r.status_code in (403, 404)


def test_forgot_password_sends_mail_for_known_address(client, monkeypatch):
    from web import reset_mail

    _admin(client)
    client.put("/api/auth/profile", json={"email": "owner@example.com", "current_password": ADMIN_PW}, headers=JSON)
    sent = []
    monkeypatch.setattr(reset_mail, "send_password_reset_email", lambda **kw: sent.append(kw))
    client.post("/api/auth/forgot-password", json={"email": "owner@example.com"}, headers=JSON)
    assert len(sent) == 1 and sent[0]["token"]


def test_reset_password_with_real_token(client, monkeypatch):
    from web.auth import create_password_reset
    from ledger.db import connect_db
    from web import app as web_app

    conn = connect_db(__import__("os").environ["STORAGE_DB_PATH"])
    try:
        uid = conn.execute("SELECT id FROM web_users WHERE username='admin'").fetchone()[0]
        token = create_password_reset(conn, uid)
    finally:
        conn.close()
    r = client.post("/api/auth/reset-password", json={"token": token, "new_password": "reset-new-pass-1"}, headers=JSON)
    assert r.status_code == 200
    assert client.post("/api/auth/login", json={"username": "admin", "password": "reset-new-pass-1"}).status_code == 200


def test_root_serves_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_static_assets_are_served(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_unknown_api_route_is_404(client):
    _admin(client)
    assert client.get("/api/does-not-exist").status_code == 404
