"""HTTP API: sign-in, sessions, owner scoping, lockout and password reset."""

from __future__ import annotations

import getpass

import pytest
from fastapi.testclient import TestClient

from config import OWNER_A, OWNER_B
from ledger.db import connect_db
from web import auth
from web import set_password
from web.app import app

ADMIN_PW = "admin-passphrase-1"
HOUSEHOLD_B_PW = "household-b-pass-1"


def _set_password(monkeypatch, argv, password):
    answers = iter([password, password])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert set_password.main(argv) == 0


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    auth._LOGIN_ATTEMPTS.clear()
    auth._RESET_ATTEMPTS.clear()
    yield
    auth._LOGIN_ATTEMPTS.clear()
    auth._RESET_ATTEMPTS.clear()


@pytest.fixture
def accounts(demo_db, monkeypatch):
    """The demo database with an admin and an owner-B user, both with passwords."""
    _set_password(monkeypatch, ["admin", "--create"], ADMIN_PW)
    _set_password(monkeypatch, ["household_b", "--create", "--owner", OWNER_B], HOUSEHOLD_B_PW)
    return demo_db


@pytest.fixture
def client(accounts):
    with TestClient(app) as c:
        yield c


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_health_is_open(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_data_endpoints_require_sign_in(client):
    for path in ("/api/meta", "/api/funds", "/api/portfolio", "/api/net-worth"):
        assert client.get(path).status_code == 401, path


def test_wrong_password_is_rejected(client):
    assert _login(client, "admin", "not-the-password").status_code == 401


def test_unknown_user_gets_same_reply_as_wrong_password(client):
    a = _login(client, "nobody-here", "whatever-pass")
    b = _login(client, "admin", "whatever-pass")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_login_sets_httponly_session_cookie(client):
    r = _login(client, "admin", ADMIN_PW)
    assert r.status_code == 200
    cookie = r.headers.get("set-cookie", "")
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert client.get("/api/auth/me").json()["username"] == "admin"


def test_admin_sees_every_fund(client):
    _login(client, "admin", ADMIN_PW)
    names = {f["name"] for f in client.get("/api/funds").json()["funds"]}
    assert {"Fidelity", "UK Super", "Super A", "Fidelity Account B"} <= names


def test_owner_user_sees_only_their_funds(client):
    _login(client, "household_b", HOUSEHOLD_B_PW)
    funds = client.get("/api/funds").json()["funds"]
    assert funds, "owner user should still see their own fund"
    assert {f["owner"] for f in funds} == {OWNER_B}


def test_owner_user_cannot_read_another_owners_trades(client):
    _login(client, "household_b", HOUSEHOLD_B_PW)
    r = client.get("/api/funds/Fidelity/trades")
    assert r.status_code in (403, 404)


def test_owner_user_cannot_add_trade_to_another_owners_fund(client):
    _login(client, "household_b", HOUSEHOLD_B_PW)
    body = {"fund_name": "Fidelity", "ticker": "AAPL", "side": "buy", "shares": 1, "price": 100}
    r = client.post("/api/trades", json=body)
    assert r.status_code in (403, 404)


def test_logout_ends_the_session(client):
    _login(client, "admin", ADMIN_PW)
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_sign_in_is_rate_limited(client):
    for _ in range(auth._LOGIN_MAX_ATTEMPTS):
        assert _login(client, "admin", "wrong-password-x").status_code == 401
    assert _login(client, "admin", "wrong-password-x").status_code == 429


def test_forgot_password_reply_does_not_reveal_accounts(client, monkeypatch):
    sent = []
    from web import reset_mail

    monkeypatch.setattr(reset_mail, "send_password_reset_email", lambda **kw: sent.append(kw))
    known = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == 200
    assert sent == []  # no account has an email on file, so nothing is sent


def test_reset_password_rejects_bad_token(client):
    r = client.post("/api/auth/reset-password", json={"token": "bogus-token-value-that-is-long", "new_password": "a-new-pass-9"})
    assert r.status_code == 400


def test_change_password_requires_current_password(client):
    _login(client, "admin", ADMIN_PW)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong-current", "new_password": "another-pass-1"},
    )
    assert r.status_code == 400


def test_security_headers_are_set(client):
    """Expected after the audit fixes: framing, MIME sniffing and CSP headers."""
    r = client.get("/api/health")
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert "content-security-policy" in {k.lower() for k in r.headers}
