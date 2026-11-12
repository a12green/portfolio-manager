"""Hargreaves Lansdown investment reports and Fidelity portfolio-balance PDFs.

The PDFs are generated here with reportlab, laid out with only the lines the
parsers look for. They are not copies of any real statement.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from ledger.db import connect_db
from ledger.trades_db import init_trade_schema
from importers.import_hl_reports import (
    collect_hl_report_paths,
    import_hl_investment_reports,
    parse_hl_investment_report,
)
from importers.import_pdf_balances import import_portfolio_balances_pdf, parse_portfolio_balances_pdf


def _pdf(path: Path, lines: list[str]) -> Path:
    c = canvas.Canvas(str(path))
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 14
        if y < 40:
            c.showPage()
            y = 800
    c.save()
    return path


def _hl_report(path: Path, *, with_holding: bool = True) -> Path:
    lines = [
        "Hargreaves Lansdown SIPP - investment report (sample, fictional)",
        "TOTAL SIPP 31 MARCH 2026",
        "10,000 12,345.67 TOTAL SIPP",
        "DETAILED VALUATION",
        "Quantity",
        "Value",
        "Cost",
        "Gain",
        "Gain %",
        "Price",
        "Holding",
    ]
    if with_holding:
        lines += [
            "10",       # quantity
            "1,200.00", # value
            "1,000.00", # cost
            "200.00",   # gain
            "20.00",    # gain %
            "12000",    # price in pence
            "NVIDIA CORP",
        ]
    lines += [
        "Subtotal",
        "1,200.00",
        # The parser reads cash balances inside the valuation block, before the
        # transactions heading. Layout assumed from the parser; confirm on a real report.
        "Capital Account Balance",
        "300.00",
        "Income Account Balance",
        "45.00",
        "CAPITAL ACCOUNT TRANSACTIONS",
    ]
    return _pdf(path, lines)


def _balances_pdf(path: Path) -> Path:
    return _pdf(
        path,
        [
            "Fidelity portfolio balances (sample, fictional)",
            "As of Jul-24-2026",
            "Z12345678",
            "$5,000.00",
            "Balance",
            "$5,000.00",
            "Net debit",
            "-$120.00",
            "Cash market value",
            "$1,000.00",
            "Margin market value",
            "$4,000.00",
            "Settled cash",
            "$250.00",
        ],
    )


# --- Hargreaves Lansdown reports ----------------------------------------------

def test_hl_report_is_parsed(tmp_path):
    snap = parse_hl_investment_report(_hl_report(tmp_path / "hl_report.pdf"))
    assert snap is not None
    assert snap.snapshot_date == date(2026, 3, 31)
    assert snap.nav == pytest.approx(12345.67)
    assert len(snap.holdings) == 1
    assert snap.holdings[0].ticker == "NVDA"
    assert snap.holdings[0].price_gbp == pytest.approx(120.0)
    assert snap.capital_cash == pytest.approx(300.0)
    assert snap.income_cash == pytest.approx(45.0)


def test_hl_report_without_a_date_is_skipped(tmp_path):
    path = _pdf(tmp_path / "not_hl.pdf", ["Some other statement", "Nothing to see"])
    assert parse_hl_investment_report(path) is None


def test_hl_report_with_no_recognised_holdings(tmp_path):
    snap = parse_hl_investment_report(_hl_report(tmp_path / "empty.pdf", with_holding=False))
    assert snap is not None
    assert snap.holdings == ()


def test_collect_hl_report_paths_finds_pdfs_in_folders(tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    _hl_report(folder / "SIPP Investment Report.pdf")
    (folder / "notes.txt").write_text("ignore me")
    found = collect_hl_report_paths([folder])
    assert [p.name for p in found] == ["SIPP Investment Report.pdf"]


def test_hl_reports_import_writes_snapshot_and_marks(db_path, tmp_path):
    pdf = _hl_report(tmp_path / "hl_report.pdf")
    conn = connect_db(db_path)
    try:
        init_trade_schema(conn)
        counts = import_hl_investment_reports(conn, [pdf])
        conn.commit()
        marks = conn.execute("SELECT COUNT(*) FROM fund_ticker_marks").fetchone()[0]
    finally:
        conn.close()
    assert isinstance(counts, dict)
    assert marks >= 1


# --- Fidelity portfolio balances ------------------------------------------------

def test_balances_pdf_is_parsed(tmp_path):
    parsed = parse_portfolio_balances_pdf(_balances_pdf(tmp_path / "balances.pdf"), fund_name="Fidelity")
    assert parsed.account_id == "Z12345678"
    assert parsed.balance_date == date(2026, 7, 24)
    assert parsed.nav == pytest.approx(5000.0)
    assert parsed.cash_balance == pytest.approx(-120.0)  # net debit wins over settled cash
    assert parsed.positions_value == pytest.approx(5000.0)  # cash market value + margin market value


def test_balances_pdf_falls_back_to_settled_cash(tmp_path):
    path = _pdf(tmp_path / "settled.pdf", ["As of Jul-24-2026", "Balance", "$900.00", "Settled cash", "$250.00"])
    parsed = parse_portfolio_balances_pdf(path, fund_name="Fidelity")
    assert parsed.cash_balance == pytest.approx(250.0)


def test_balances_pdf_needs_a_fund_name(tmp_path):
    with pytest.raises(ValueError):
        parse_portfolio_balances_pdf(_balances_pdf(tmp_path / "b.pdf"), fund_name="  ")


def test_balances_pdf_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_portfolio_balances_pdf(tmp_path / "missing.pdf", fund_name="Fidelity")


def test_balances_pdf_without_balance_raises(tmp_path):
    path = _pdf(tmp_path / "nobalance.pdf", ["Nothing useful here"])
    with pytest.raises(ValueError):
        parse_portfolio_balances_pdf(path, fund_name="Fidelity")


def test_balances_import_stores_a_mark(demo_db, tmp_path):
    pdf = _balances_pdf(tmp_path / "balances.pdf")
    conn = connect_db(demo_db)
    try:
        init_trade_schema(conn)
        import_portfolio_balances_pdf(conn, pdf, fund_name="Fidelity")
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) FROM fund_manual_balances m JOIN trade_funds f ON f.id = m.fund_id WHERE f.name = 'Fidelity'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n >= 1
