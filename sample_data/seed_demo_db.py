"""Load the sample broker exports and statements into a demo database.

    python sample_data/seed_demo_db.py                 # writes data/demo.sqlite3
    python sample_data/seed_demo_db.py path/to/demo.sqlite3

Then run the web UI against it:

    STORAGE_DB_PATH=data/demo.sqlite3 python -m web

Re-running replaces the demo database at that path. It never touches the
database the app uses by default, so point it only at a demo file.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from importers.import_aussuper import import_aussuper_csv  # noqa: E402
from importers.import_aussuper_statements import import_aussuper_statements  # noqa: E402
from importers.import_fidelity import import_fidelity_csv  # noqa: E402
from importers.import_fidelity_statements import import_fidelity_statements  # noqa: E402
from importers.import_hl import import_hl_csv  # noqa: E402
from ledger.db import connect_db, init_schema  # noqa: E402
from ledger.trades_db import init_trade_schema  # noqa: E402

DEFAULT_DB = ROOT / "data" / "demo.sqlite3"


def main(argv: list[str]) -> int:
    db_path = Path(argv[0]).expanduser() if argv else DEFAULT_DB
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for stale in (db_path, Path(f"{db_path}-journal"), Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if stale.exists():
            stale.unlink()

    conn = connect_db(db_path)
    try:
        init_schema(conn)
        init_trade_schema(conn)

        fid = import_fidelity_csv(conn, HERE / "fidelity" / "fidelity_llc_sample.csv")
        print("Fidelity trades:", fid)

        hl = import_hl_csv(conn, HERE / "hargreaves_lansdown" / "HL_history_sample.csv")
        print("Hargreaves Lansdown trades:", hl)

        aus = import_aussuper_csv(conn, HERE / "aussuper" / "AusSuperAG_sample.csv")
        print("AusSuper CSV:", aus)

        fid_stmt = import_fidelity_statements(
            conn, [HERE / "statements" / "fidelity" / "ACCOUNT_B" / "Statement9302026.pdf"]
        )
        print("Fidelity statements:", fid_stmt)

        aus_stmt = import_aussuper_statements(
            conn, [HERE / "statements" / "aussuper" / "AusSuper2026JUNST.pdf"]
        )
        print("AusSuper statements:", aus_stmt)

        conn.commit()
    finally:
        conn.close()

    print(f"\nDemo database written to {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
