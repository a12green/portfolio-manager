"""Password hashing, locked accounts, sessions, reset tokens and the password command."""

from __future__ import annotations

import getpass
from datetime import timedelta

import pytest

from config import OWNER_B
from ledger.db import connect_db
from web import auth
from web import set_password


@pytest.fixture
def conn(db_path):
    c = connect_db(db_path)
    auth.init_web_auth_schema(c)
    auth.seed_default_users(c)
    yield c
    c.close()


def _admin_id(conn):
    return conn.execute("SELECT id FROM web_users WHERE username='admin'").fetchone()[0]


def test_only_admin_is_seeded(conn):
    names = [r[0] for r in conn.execute("SELECT username FROM web_users")]
    assert names == ["admin"]


def test_seeded_admin_cannot_sign_in_before_password_is_set(conn):
    assert auth.authenticate_user(conn, "admin", "anything-at-all") is None
    assert auth.authenticate_user(conn, "admin", "") is None


def test_hash_round_trip_and_wrong_password():
    h = auth._hash_password("correct horse battery")
    assert h != "correct horse battery"
    assert auth._verify_password("correct horse battery", h)
    assert not auth._verify_password("correct horse batterY", h)


def test_password_command_sets_admin_password(conn, db_path, monkeypatch):
    answers = iter(["s3cure-passphrase", "s3cure-passphrase"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert set_password.main(["admin"]) == 0
    user = auth.authenticate_user(conn, "admin", "s3cure-passphrase")
    assert user is not None and user.is_admin


def test_password_command_refuses_short_password(conn, monkeypatch):
    answers = iter(["short", "short"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert set_password.main(["admin"]) != 0
    assert auth.authenticate_user(conn, "admin", "short") is None


def test_password_command_refuses_mismatched_confirmation(conn, monkeypatch):
    answers = iter(["longenough1", "longenough2"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert set_password.main(["admin"]) != 0


def test_password_command_creates_owner_scoped_user(conn, monkeypatch):
    answers = iter(["householdb-pass", "householdb-pass"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert set_password.main(["household_b", "--create", "--owner", OWNER_B]) == 0
    user = auth.authenticate_user(conn, "household_b", "householdb-pass")
    assert user is not None
    assert user.owner_scope == OWNER_B
    assert not user.is_admin


def test_password_command_rejects_unknown_owner(conn, monkeypatch):
    answers = iter(["longenough1", "longenough1"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert set_password.main(["someone", "--create", "--owner", "Nobody"]) != 0


def test_session_token_is_stored_hashed(conn):
    uid = _admin_id(conn)
    conn.execute("UPDATE web_users SET password_hash=? WHERE id=?", (auth._hash_password("pw-pw-pw-pw"), uid))
    conn.commit()
    token = auth.create_session(conn, uid)
    stored = [r[0] for r in conn.execute("SELECT token_hash FROM web_sessions")]
    assert token not in stored
    assert auth._token_hash(token) in stored
    assert auth.user_for_session_token(conn, token).username == "admin"


def test_expired_session_is_rejected(conn):
    uid = _admin_id(conn)
    conn.execute("UPDATE web_users SET password_hash=? WHERE id=?", (auth._hash_password("pw-pw-pw-pw"), uid))
    conn.commit()
    token = auth.create_session(conn, uid)
    past = auth._utcnow() - timedelta(hours=1)
    conn.execute("UPDATE web_sessions SET expires_at=?", (past.isoformat(),))
    conn.commit()
    assert auth.user_for_session_token(conn, token) is None


def test_reset_token_is_single_use(conn):
    uid = _admin_id(conn)
    token = auth.create_password_reset(conn, uid)
    auth.reset_password_with_token(conn, token, "brand-new-pass")
    assert auth.authenticate_user(conn, "admin", "brand-new-pass") is not None
    with pytest.raises(ValueError):
        auth.reset_password_with_token(conn, token, "another-pass-1")


def test_reset_token_expires(conn):
    uid = _admin_id(conn)
    token = auth.create_password_reset(conn, uid)
    conn.execute("UPDATE web_password_resets SET expires_at=?", ((auth._utcnow() - timedelta(minutes=1)).isoformat(),))
    conn.commit()
    with pytest.raises(ValueError):
        auth.reset_password_with_token(conn, token, "brand-new-pass")


def test_reset_token_stored_hashed(conn):
    uid = _admin_id(conn)
    token = auth.create_password_reset(conn, uid)
    stored = [r[0] for r in conn.execute("SELECT token_hash FROM web_password_resets")]
    assert token not in stored


def test_wrong_reset_token_is_rejected(conn):
    with pytest.raises(ValueError):
        auth.reset_password_with_token(conn, "not-a-real-token", "brand-new-pass")


def _request_with_forwarded(ip_header: str):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", ip_header.encode())],
        "client": ("198.51.100.7", 1234),
        "method": "POST",
        "path": "/",
    }
    return Request(scope)


def test_forwarded_header_is_ignored_unless_proxy_is_trusted(monkeypatch):
    monkeypatch.setattr(auth, "TRUST_PROXY", False)
    # A client rotating this header must not get fresh rate-limit buckets.
    assert auth._client_key(_request_with_forwarded("203.0.113.9")) == "198.51.100.7"


def test_forwarded_header_is_used_behind_a_trusted_proxy(monkeypatch):
    monkeypatch.setattr(auth, "TRUST_PROXY", True)
    assert auth._client_key(_request_with_forwarded("203.0.113.9, 10.0.0.1")) == "203.0.113.9"
