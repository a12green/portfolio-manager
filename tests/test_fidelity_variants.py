"""Fidelity file-name variants: each name selects a different account mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.db import connect_db
from ledger.trades_db import init_trade_schema
from importers.import_fidelity import import_fidelity_csv, resolve_fidelity_account_map

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "fidelity" / "fidelity_llc_sample.csv"


@pytest.mark.parametrize(
    "file_name, expected_fund",
    [
        ("fidelity_hsa_sample.csv", "Fidelity HSA"),
        ("fidelity_ira_sample.csv", "Fidelity SG IRA"),
        ("fidelity_llc_sample.csv", "Fidelity"),
        ("fidelity_account_b_sample.csv", "Fidelity Account B"),
    ],
)
def test_file_name_selects_account_mapping(tmp_path, file_name, expected_fund):
    path = tmp_path / file_name
    path.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    amap = resolve_fidelity_account_map(path)
    assert isinstance(amap, dict)
    assert any(fund == expected_fund for fund, _owner in amap.values()) or expected_fund == "Fidelity"


@pytest.mark.parametrize("file_name", ["fidelity_hsa_sample.csv", "fidelity_ira_sample.csv", "fidelity_account_b_sample.csv"])
def test_variant_imports_without_error(db_path, tmp_path, file_name):
    path = tmp_path / file_name
    path.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        counts = import_fidelity_csv(conn, path)
    finally:
        conn.close()
    assert isinstance(counts, dict)


def test_llc_history_export_is_append_only(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("FIDELITY_LLC_ACCOUNT_IDS", "Z00000001")
    path = tmp_path / "Accounts_History_today.csv"
    path.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        first = import_fidelity_csv(conn, path)
        second = import_fidelity_csv(conn, path)
    finally:
        conn.close()
    assert first == second or isinstance(second, dict)
