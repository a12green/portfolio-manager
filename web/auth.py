"""Web UI authentication — bcrypt passwords, server-side sessions, owner scoping."""

from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
import hashlib
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import bcrypt
from fastapi import HTTPException, Request, Response

if TYPE_CHECKING:
    from ledger.trades_db import TradeFund

SESSION_COOKIE = "portfolio_session"
SESSION_HOURS = int(os.getenv("WEB_SESSION_HOURS", "12"))
# Secure by default: the session cookie is sent only over HTTPS. Set
# WEB_SECURE_COOKIES=false only for plain-HTTP local use.
SECURE_COOKIES = os.getenv("WEB_SECURE_COOKIES", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# The only account created on first run: the admin (all owners). No password
# lives in the source. It is created locked (sign-in always fails) until you set
# one with:  python -m web.set_password admin --create
# Owner-scoped users are added the same way with --owner (see web/set_password.py).
_DEFAULT_USERS: tuple[tuple[str, str | None], ...] = (
    ("admin", None),
)
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=12)).decode("ascii")
LOCKED_PASSWORD_HASH = ""  # bcrypt rejects this, so verification always returns False

_LOGIN_ATTEMPTS: dict[str, tuple[int, float]] = {}
_LOGIN_MAX_ATTEMPTS = 8
_LOGIN_WINDOW_SEC = 900.0

# Separate bucket so reset requests cannot lock out sign-in, and vice versa.
_RESET_ATTEMPTS: dict[str, tuple[int, float]] = {}
_RESET_MAX_ATTEMPTS = 5

RESET_TOKEN_MINUTES = int(os.getenv("WEB_RESET_TOKEN_MINUTES", "30"))
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class WebUser:
    id: int
    username: str
    owner_scope: str | None  # None = admin (all owners)

    @property
    def is_admin(self) -> bool:
        return self.owner_scope is None


def _db_path() -> Path:
    import trades_cli

    return trades_cli._db_path()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def init_web_auth_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS web_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            owner_scope TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS web_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES web_users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_web_sessions_user ON web_sessions (user_id);
        CREATE INDEX IF NOT EXISTS idx_web_sessions_expires ON web_sessions (expires_at);
        CREATE TABLE IF NOT EXISTS web_user_profiles (
            user_id INTEGER PRIMARY KEY,
            email TEXT UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES web_users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS web_password_resets (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES web_users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_web_resets_user ON web_password_resets (user_id);
        """
    )
    conn.commit()


def seed_default_users(conn: sqlite3.Connection) -> None:
    """Create default users on first run only.

    This runs on every request (see web/app.py:_db_conn), so it must never touch
    an existing user's password_hash — otherwise a self-service password change
    (see change_password()) would get silently reverted back to the hardcoded
    _DEFAULT_USERS password on the very next request.
    """
    init_web_auth_schema(conn)
    now = _utcnow().isoformat(timespec="seconds")
    for username, owner_scope in _DEFAULT_USERS:
        row = conn.execute(
            "SELECT id FROM web_users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if row is not None:
            continue
        conn.execute(
            """
            INSERT INTO web_users (username, password_hash, owner_scope, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (username.lower(), LOCKED_PASSWORD_HASH, owner_scope, now),
        )
    conn.commit()


TRUST_PROXY = os.getenv("WEB_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes", "on")


def _client_key(request: Request) -> str:
    # X-Forwarded-For is client-controlled. Only believe it when a proxy we run sets it.
    if TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    if request.client:
        return request.client.host
    return "unknown"


def _check_rate_limit(
    request: Request,
    bucket: dict[str, tuple[int, float]] | None = None,
    max_attempts: int | None = None,
) -> None:
    bucket = _LOGIN_ATTEMPTS if bucket is None else bucket
    max_attempts = _LOGIN_MAX_ATTEMPTS if max_attempts is None else max_attempts
    key = _client_key(request)
    now = time.monotonic()
    count, start = bucket.get(key, (0, now))
    if now - start > _LOGIN_WINDOW_SEC:
        count, start = 0, now
    if count >= max_attempts:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
    bucket[key] = (count + 1, start)


def _clear_rate_limit(request: Request) -> None:
    _LOGIN_ATTEMPTS.pop(_client_key(request), None)


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address")
    return email


def get_profile(conn: sqlite3.Connection, user_id: int) -> dict[str, str | None]:
    row = conn.execute(
        "SELECT email, display_name FROM web_user_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return {"email": None, "display_name": ""}
    return {"email": row[0], "display_name": row[1] or ""}


def set_profile(
    conn: sqlite3.Connection, user_id: int, email: str | None, display_name: str
) -> None:
    """Store profile fields. email=None clears it. Raises ValueError if already in use."""
    now = _utcnow().isoformat(timespec="seconds")
    try:
        conn.execute(
            """
            INSERT INTO web_user_profiles (user_id, email, display_name, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                email = excluded.email,
                display_name = excluded.display_name,
                updated_at = excluded.updated_at
            """,
            (user_id, email, display_name, now),
        )
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ValueError("That email address is already in use") from e
    conn.commit()


def verify_current_password(conn: sqlite3.Connection, user: WebUser, password: str) -> bool:
    row = conn.execute(
        "SELECT password_hash FROM web_users WHERE id = ?", (user.id,)
    ).fetchone()
    return row is not None and _verify_password(password, str(row[0]))


def find_user_by_email(conn: sqlite3.Connection, email: str) -> WebUser | None:
    row = conn.execute(
        """
        SELECT u.id, u.username, u.owner_scope
        FROM web_user_profiles p
        JOIN web_users u ON u.id = p.user_id
        WHERE p.email = ? COLLATE NOCASE
        """,
        (email,),
    ).fetchone()
    return None if row is None else _user_from_row(row)


def create_password_reset(conn: sqlite3.Connection, user_id: int) -> str:
    """Issue a single-use reset token (returned once; only its hash is stored)."""
    token = secrets.token_urlsafe(32)
    now = _utcnow()
    # Invalidate any earlier unused links for this user.
    conn.execute(
        "DELETE FROM web_password_resets WHERE user_id = ? AND used_at IS NULL",
        (user_id,),
    )
    conn.execute(
        """
        INSERT INTO web_password_resets (token_hash, user_id, expires_at, used_at, created_at)
        VALUES (?, ?, ?, NULL, ?)
        """,
        (
            _token_hash(token),
            user_id,
            (now + timedelta(minutes=RESET_TOKEN_MINUTES)).isoformat(),
            now.isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return token


def reset_password_with_token(conn: sqlite3.Connection, token: str, new_password: str) -> None:
    """Set a new password using a reset token. Raises ValueError if the token is unusable."""
    now = _utcnow()
    row = conn.execute(
        "SELECT user_id, expires_at, used_at FROM web_password_resets WHERE token_hash = ?",
        (_token_hash(token),),
    ).fetchone()
    if row is None or row[2] is not None or str(row[1]) < now.isoformat():
        raise ValueError("This reset link is invalid or has expired. Request a new one.")
    user_id = int(row[0])
    conn.execute(
        "UPDATE web_users SET password_hash = ? WHERE id = ?",
        (_hash_password(new_password), user_id),
    )
    conn.execute(
        "UPDATE web_password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
        (now.isoformat(timespec="seconds"), user_id),
    )
    # Reset means every existing session is no longer trusted.
    conn.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
    conn.commit()


def _user_from_row(row: sqlite3.Row | tuple) -> WebUser:
    return WebUser(id=int(row[0]), username=str(row[1]), owner_scope=row[2])


def authenticate_user(conn: sqlite3.Connection, username: str, password: str) -> WebUser | None:
    row = conn.execute(
        """
        SELECT id, username, owner_scope, password_hash
        FROM web_users WHERE username = ? COLLATE NOCASE
        """,
        (username.strip(),),
    ).fetchone()
    if row is None:
        _verify_password(password, _DUMMY_HASH)  # same cost as a real check
        return None
    if not _verify_password(password, str(row[3])):
        return None
    return _user_from_row(row)


def change_password(
    conn: sqlite3.Connection, user: WebUser, current_password: str, new_password: str
) -> None:
    """Verify the current password and set a new one. Raises ValueError if it doesn't match."""
    row = conn.execute(
        "SELECT password_hash FROM web_users WHERE id = ?",
        (user.id,),
    ).fetchone()
    if row is None or not _verify_password(current_password, str(row[0])):
        raise ValueError("Current password is incorrect")
    conn.execute(
        "UPDATE web_users SET password_hash = ? WHERE id = ?",
        (_hash_password(new_password), user.id),
    )
    conn.commit()


def delete_other_sessions(conn: sqlite3.Connection, user_id: int, keep_token: str | None) -> None:
    """Sign a user out everywhere else — used after a password change."""
    if keep_token:
        conn.execute(
            "DELETE FROM web_sessions WHERE user_id = ? AND token_hash != ?",
            (user_id, _token_hash(keep_token)),
        )
    else:
        conn.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
    conn.commit()


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = _utcnow() + timedelta(hours=SESSION_HOURS)
    now = _utcnow().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO web_sessions (token_hash, user_id, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (_token_hash(token), user_id, expires.isoformat(), now),
    )
    conn.commit()
    return token


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (_token_hash(token),))
    conn.commit()


def purge_expired_sessions(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM web_sessions WHERE expires_at < ?",
        (_utcnow().isoformat(),),
    )
    conn.commit()


def user_for_session_token(conn: sqlite3.Connection, token: str | None) -> WebUser | None:
    if not token:
        return None
    purge_expired_sessions(conn)
    row = conn.execute(
        """
        SELECT u.id, u.username, u.owner_scope
        FROM web_sessions s
        JOIN web_users u ON u.id = s.user_id
        WHERE s.token_hash = ? AND s.expires_at >= ?
        """,
        (_token_hash(token), _utcnow().isoformat()),
    ).fetchone()
    if row is None:
        return None
    return _user_from_row(row)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        max_age=SESSION_HOURS * 3600,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def allowed_owner_names(user: WebUser) -> list[str]:
    if user.is_admin:
        return ["All", OWNER_A, OWNER_B, OWNER_C]
    assert user.owner_scope is not None
    return [user.owner_scope]


def resolve_owner(user: WebUser, owner: str) -> str:
    """Validate API owner parameter against the logged-in user's scope."""
    raw = (owner or "").strip()
    if user.is_admin:
        if not raw or raw.lower() == "all":
            return "All"
        if raw in (OWNER_A, OWNER_B, OWNER_C):
            return raw
        raise HTTPException(status_code=400, detail=f"Unknown owner: {owner!r}")
    scope = user.owner_scope
    assert scope is not None
    if not raw or raw.lower() == "all" or raw == scope:
        return scope
    raise HTTPException(status_code=403, detail="You do not have access to that owner.")


def assert_fund_access(user: WebUser, fund: "TradeFund") -> None:
    if user.is_admin:
        return
    if fund.owner != user.owner_scope:
        raise HTTPException(status_code=403, detail="You do not have access to that fund.")


def assert_owner_field(user: WebUser, owner: str) -> None:
    if user.is_admin:
        return
    if owner.strip() != user.owner_scope:
        raise HTTPException(status_code=403, detail="You may only use your own owner.")
