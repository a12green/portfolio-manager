"""Run the web UI: python -m web"""

from __future__ import annotations

import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()


def _warn_if_admin_locked() -> None:
    """Tell the user, before serving, if the admin password has not been set yet."""
    import trades_cli
    from ledger.db import connect_db
    from trades_db import init_trade_schema
    from web import auth

    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        auth.seed_default_users(conn)
        row = conn.execute(
            "SELECT password_hash FROM web_users WHERE username = 'admin' COLLATE NOCASE"
        ).fetchone()
        if not row or not row[0]:
            print(
                "\n⚠️  The admin account has no password yet, so nobody can sign in.\n"
                "   Set it now:  python -m web.set_password admin\n"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    _warn_if_admin_locked()
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8080"))
    uvicorn.run(
        "web.app:app",
        host=host,
        port=port,
        reload=os.getenv("WEB_RELOAD", "").lower() in ("1", "true", "yes"),
    )
