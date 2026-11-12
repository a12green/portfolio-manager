"""Each broker importer, run against the fictional sample files."""

from __future__ import annotations

import pytest

from config import OWNER_A, OWNER_B
from ledger.db import connect_db
from ledger.trades_db import init_trade_schema
from importers.import_aussuper import import_aussuper_csv
from importers.import_fidelity import import_fidelity_csv
from importers.import_fidelity_statements import import_fidelity_statements
from importers.import_hl import import_hl_csv
from importers.import_aussuper_statements import import_aussuper_statements
from importers.import_fidelity_statements import parse_fidelity_statement_pdf

from pathlib import Path

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data"


@pytest.fixture
def conn(db_path):
    c = connect_db(db_path)
    init_trade_schema(c)
    yield c
    c.close()


def _fund_owners(conn):
    return dict(conn.execute("SELECT name, owner FROM trade_funds").fetchall())


def test_fidelity_csv_creates_fund_and_trades(conn):
    counts = import_fidelity_csv(conn, SAMPLE / "fidelity" / "fidelity_llc_sample.csv")
    assert counts["Fidelity"] == 6
    assert counts["Fidelity cash"] == 2
    assert _fund_owners(conn)["Fidelity"] == OWNER_A


def test_fidelity_csv_is_idempotent(conn):
    path = SAMPLE / "fidelity" / "fidelity_llc_sample.csv"
    import_fidelity_csv(conn, path)
    first = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    import_fidelity_csv(conn, path)
    second = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert first == second == 6


def test_hl_csv_imports_gbp_trades_for_owner_a(conn, offline_fx):
    assert import_hl_csv(conn, SAMPLE / "hargreaves_lansdown" / "HL_history_sample.csv") == 4
    owners = _fund_owners(conn)
    assert owners["UK Super"] == OWNER_A
    sides = [r[0] for r in conn.execute("SELECT side FROM trades WHERE fund_id = (SELECT id FROM trade_funds WHERE name='UK Super')")]
    assert sorted(sides) == ["buy", "buy", "buy", "sell"]


def test_aussuper_csv_imports_trades_and_cash(conn):
    result = import_aussuper_csv(conn, SAMPLE / "aussuper" / "AusSuperAG_sample.csv")
    assert result.trades == 4
    assert result.cash_events == 4
    assert result.opening_cash == pytest.approx(10000.0)
    assert _fund_owners(conn)["Super A"] == OWNER_A


def test_fidelity_statement_is_parsed_for_owner_b():
    snaps = parse_fidelity_statement_pdf(SAMPLE / "statements" / "fidelity" / "ACCOUNT_B" / "Statement9302026.pdf")
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap.fund_name == "Fidelity Account B"
    assert snap.owner == OWNER_B
    assert snap.nav == pytest.approx(48215.60)
    assert snap.cash_balance == pytest.approx(2410.78)


def test_fidelity_statement_without_account_b_marker_is_ignored(tmp_path):
    # A statement with neither the ACCOUNT_B folder nor the holder line is not this account.
    from reportlab.pdfgen import canvas

    pdf_path = tmp_path / "Statement9302026.pdf"
    pdf = canvas.Canvas(str(pdf_path))
    for i, line in enumerate(["Total Holdings", "$1,000.00", "Account: SOMEONE ELSE"]):
        pdf.drawString(50, 700 - 18 * i, line)
    pdf.save()
    assert parse_fidelity_statement_pdf(pdf_path) == []


def test_fidelity_statement_import_writes_snapshot(conn):
    counts = import_fidelity_statements(
        conn, [SAMPLE / "statements" / "fidelity" / "ACCOUNT_B" / "Statement9302026.pdf"]
    )
    assert counts == {"Fidelity Account B": 1}
    assert _fund_owners(conn)["Fidelity Account B"] == OWNER_B


def test_aussuper_statement_import_writes_snapshot(conn):
    counts = import_aussuper_statements(conn, [SAMPLE / "statements" / "aussuper" / "AusSuper2026JUNST.pdf"])
    assert counts == {"Super A": 1}
