"""Google Sheets-backed features, run against the synthetic sheet in conftest."""

from __future__ import annotations

import sys
from datetime import date

import pytest

import main
from config import OWNER_A, OWNER_B, OWNER_C
from ledger.db import connect_db


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("LIVE_QUOTES", "false")
    monkeypatch.setenv("MARKET_HOLIDAYS_ENABLED", "false")


def test_portfolio_dataframe_comes_from_sheet(fake_sheets):
    df = main.load_portfolio_dataframe()
    assert len(df) == 5
    assert set(df["Symbol"]) >= {"NVDA", "AAPL", "MSFT"}


def test_sheet_loader_requires_spreadsheet_id(monkeypatch, fake_sheets):
    monkeypatch.delenv("GOOGLE_SHEETS_SPREADSHEET_ID", raising=False)
    with pytest.raises(ValueError, match="GOOGLE_SHEETS_SPREADSHEET_ID"):
        main.load_sheet_dataframe()


def test_sheet_loader_rejects_missing_key_file(monkeypatch, fake_sheets, tmp_path):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(tmp_path / "nope.json"))
    with pytest.raises(FileNotFoundError):
        main.load_sheet_dataframe()


def test_sheet_shares_for_owner(fake_sheets, monkeypatch):
    import reports.portfolio_data as portfolio_data
    from reports.portfolio_data import sheet_shares_for_owner

    monkeypatch.setattr(portfolio_data, "_sheet_cache", None)  # do not reuse an earlier test's cache
    shares = sheet_shares_for_owner(OWNER_A)
    assert shares.get("NVDA") == pytest.approx(10.0)


def test_fund_lines_from_sheet(fake_sheets):
    from importers.funds import load_fund_lines_from_sheet

    lines = load_fund_lines_from_sheet(date(2026, 11, 9))
    assert set(lines) == {OWNER_A, OWNER_B, OWNER_C}
    owner_a_rows = next(iter(lines.values()))
    assert owner_a_rows and owner_a_rows[0].fund_name == "Fidelity"


def test_fund_owner_amounts_from_sheet(fake_sheets):
    from importers.funds import load_fund_owner_amounts_from_sheet

    amounts = load_fund_owner_amounts_from_sheet()
    assert isinstance(amounts, dict) and amounts


def test_fund_lines_need_a_week_date(fake_sheets):
    from importers.funds import load_fund_lines_from_sheet

    with pytest.raises(TypeError):
        load_fund_lines_from_sheet("2026-11-09")


def test_compare_ledger_to_sheet(demo_db, fake_sheets):
    from reports.holdings_compare import compare_ledger_to_sheet, format_discrepancy_summary

    rows = compare_ledger_to_sheet(demo_db, owner=OWNER_A)
    assert isinstance(rows, list)
    assert isinstance(format_discrepancy_summary(rows), str)


def test_portfolio_holdings_with_sheet_comparison(demo_db, fake_sheets):
    from reports.portfolio_data import load_portfolio_holdings

    cards, *_rest = load_portfolio_holdings(
        demo_db, owner=OWNER_A, fund_filter=None, market_sel="All",
        use_live=False, owner_label=OWNER_A, include_sheet_compare=True,
    )
    assert isinstance(cards, list)


def test_stock_snapshot_from_sheet_via_cli(demo_db, fake_sheets, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--snapshot-stocks-daily"])
    try:
        main.main()
    except SystemExit as e:
        assert e.code in (0, None)
    conn = connect_db(demo_db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM stock_daily_snapshots").fetchone()[0]
    finally:
        conn.close()
    assert n >= 1


def test_weekly_fund_snapshot_from_sheet_via_cli(demo_db, fake_sheets, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--snapshot-funds-weekly"])
    try:
        main.main()
    except SystemExit as e:
        assert e.code in (0, None)


def test_fund_list_cli_reads_sheet(demo_db, fake_sheets, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--fund-list"])
    try:
        main.main()
    except SystemExit:
        pass
    assert "Fidelity" in capsys.readouterr().out
