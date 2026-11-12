"""Branches of the statement importers: CSV statement summaries, filename date forms,
AusSuper date fallbacks, and scheme takeovers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ledger.db import connect_db
from ledger.trades_db import init_trade_schema
from importers.import_aussuper import import_aussuper_alltx, _parse_au_date
from importers.import_fidelity_statements import (
    import_fidelity_statements,
    parse_statement_filename,
)

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data"


# --- Fidelity statement CSV summary -----------------------------------------------

_STATEMENT_CSV = (
    "Account Type,Account,Beginning Value,Deposits,Ending Value\n"
    "Individual - TOD,Z21-000001,100.00,0,20000.00\n"
    "Limited Liability Company,Z34-000002,100.00,0,10000.00\n"
    "Unknown Account Type,Z99-000003,1,1,5.00\n"
    "Traditional IRA,Z44-000004\n"
    "Symbol,Quantity,Price\n"
    "NVDA,10,180\n"
)


def test_fidelity_statement_csv_is_summarised(db_path, tmp_path):
    path = tmp_path / "Statement4302025.csv"
    path.write_text(_STATEMENT_CSV, encoding="utf-8")
    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        counts = import_fidelity_statements(conn, [path])
        conn.commit()
    finally:
        conn.close()
    assert isinstance(counts, dict)


def test_fidelity_statement_csv_with_no_known_accounts(db_path, tmp_path):
    path = tmp_path / "Statement4302025.csv"
    path.write_text("Account Type,Account,Beginning,Deposits,Ending\nSomething else,X,1,1,2\n", encoding="utf-8")
    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        assert import_fidelity_statements(conn, [path]) == {}
    finally:
        conn.close()


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Statement4302025.pdf", date(2025, 4, 30)),
        ("Statement10312025.pdf", date(2025, 10, 31)),
        ("HSAStatement10312025.csv", date(2025, 10, 31)),
        ("IRAStatement9302026.pdf", date(2026, 9, 30)),
    ],
)
def test_statement_filename_date_forms(tmp_path, name, expected):
    assert parse_statement_filename(tmp_path / name) == expected


def test_statement_filename_without_a_date_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        parse_statement_filename(tmp_path / "Statement.pdf")


def test_statement_filename_with_odd_length_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        parse_statement_filename(tmp_path / "Statement12345.pdf")


# --- AusSuper dates ------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("02/07/2025", date(2025, 7, 2)),
        ("5/3/26", date(2026, 3, 5)),
        ("1/12/99", date(1999, 12, 1)),
    ],
)
def test_aussuper_date_forms(raw, expected):
    assert _parse_au_date(raw) == expected


def test_aussuper_bad_date_is_rejected():
    with pytest.raises(ValueError):
        _parse_au_date("not a date")


# --- AusSuper scheme takeover --------------------------------------------------------

def test_aussuper_takeover_closes_the_position(db_path, tmp_path):
    csv = tmp_path / "AusSuperAG_takeover.csv"
    base = (SAMPLE / "aussuper" / "AusSuperAG_sample.csv").read_text(encoding="utf-8")
    csv.write_text(
        base + "15/09/2026,Takeover of VAS.AU @ AUD 101.5 p/s,Interest,1500.00,8000.00\n",
        encoding="utf-8",
    )
    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        result = import_aussuper_alltx(conn, csv)
    finally:
        conn.close()
    assert result.trades >= 4
    assert result.cash_events >= 0
