"""Shared fixtures. Every test gets its own temporary database and offline prices."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "sample_data"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A fresh, empty app database with all schemas created."""
    from ledger.db import connect_db, init_schema
    from ledger.trades_db import init_trade_schema

    path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("STORAGE_DB_PATH", str(path))
    conn = connect_db(path)
    try:
        init_schema(conn)
        init_trade_schema(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def offline_fx(monkeypatch):
    """Replace the GBP/USD lookup with a fixed rate so no network call is made."""
    import importers.import_hl as import_hl

    monkeypatch.setattr(
        import_hl,
        "_gbp_usd_rates_for_dates",
        lambda dates: {d: 1.25 for d in dates},
    )


@pytest.fixture
def demo_db(db_path, offline_fx):
    """The database produced by the sample-data loader."""
    from sample_data.seed_demo_db import main as seed

    assert seed([str(db_path)]) == 0
    return db_path
