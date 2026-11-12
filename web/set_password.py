"""Set or reset a web UI login password from the terminal.

The password is read with a hidden prompt, never taken as an argument, so it
stays out of shell history and process listings.

    python -m web.set_password admin --create          # first admin, on first run
    python -m web.set_password newperson --create --owner "Household B"

Owner scope: omit --owner for an admin (all owners); otherwise one of the owners in settings.json.
"""

from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
import argparse
import getpass
import sys

import trades_cli
from ledger.db import connect_db
from ledger.trades_db import init_trade_schema
from web import auth

VALID_OWNERS = (OWNER_A, OWNER_B, OWNER_C)
MIN_LENGTH = 8


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set a web UI login password.")
    parser.add_argument("username")
    parser.add_argument("--create", action="store_true", help="create the account if it does not exist")
    parser.add_argument("--owner", default=None, help="owner scope: one of the owners in settings.json (omit for admin)")
    args = parser.parse_args(argv)

    if args.owner is not None and args.owner not in VALID_OWNERS:
        print(f"--owner must be one of: {', '.join(VALID_OWNERS)}", file=sys.stderr)
        return 2

    password = getpass.getpass("New password: ")
    if len(password) < MIN_LENGTH:
        print(f"Password must be at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 2
    if getpass.getpass("Confirm new password: ") != password:
        print("Passwords do not match. Nothing changed.", file=sys.stderr)
        return 2

    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        auth.init_web_auth_schema(conn)
        username = args.username.strip()
        row = conn.execute(
            "SELECT id FROM web_users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        new_hash = auth._hash_password(password)
        if row is None:
            if not args.create:
                print(f"No account named {username!r}. Use --create to make it.", file=sys.stderr)
                return 1
            now = auth._utcnow().isoformat(timespec="seconds")
            conn.execute(
                """
                INSERT INTO web_users (username, password_hash, owner_scope, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (username.lower(), new_hash, args.owner, now),
            )
            print(f"Created account {username.lower()!r} and set its password.")
        else:
            user_id = int(row[0])
            conn.execute(
                "UPDATE web_users SET password_hash = ? WHERE id = ?", (new_hash, user_id)
            )
            # Changing a password signs the account out everywhere.
            conn.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
            print(f"Password set for {username!r}. All its sessions were signed out.")
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
