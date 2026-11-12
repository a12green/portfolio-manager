"""Holdings come from a local CSV in the Stocks.csv layout."""

from __future__ import annotations

import sys

import pytest

import main
from config import OWNER_A, OWNER_B
from ledger.db import connect_db


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("LIVE_QUOTES", "false")
    monkeypatch.setenv("MARKET_HOLIDAYS_ENABLED", "false")


def test_holdings_are_read_from_the_configured_csv(holdings_csv):
    df = main.load_portfolio_dataframe()
    assert set(df["Symbol"]) >= {"NVDA", "AAPL", "MSFT"}


def test_holdings_are_grouped_by_owner_block(holdings_csv):
    work = main._prepare_holdings(main.load_portfolio_dataframe())
    by_owner = {p: sorted(sub["Ticker"]) for p, sub in work.groupby("Portfolio")}
    assert by_owner == {OWNER_A: ["AAPL", "NVDA"], OWNER_B: ["MSFT"]}


def test_missing_holdings_source_gives_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("PORTFOLIO_CSV_PATH", raising=False)
    monkeypatch.setattr(main, "_project_dir", lambda: tmp_path)  # no Stocks.csv here
    with pytest.raises(ValueError, match="No holdings file configured"):
        main.load_portfolio_dataframe()


def test_stock_snapshot_writes_rows_from_the_csv(demo_db, holdings_csv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--snapshot-stocks-daily"])
    try:
        main.main()
    except SystemExit as e:
        assert e.code in (0, None)
    conn = connect_db(demo_db)
    try:
        tickers = {r[0] for r in conn.execute("SELECT ticker FROM stock_daily_snapshots")}
    finally:
        conn.close()
    assert {"NVDA", "AAPL", "MSFT"} <= tickers


def test_heatmap_tiles_come_from_the_csv_for_owner_without_ledger(demo_db, holdings_csv):
    from reports.heatmap_data import _tiles_from_holdings_file

    tiles = _tiles_from_holdings_file(OWNER_B)
    assert [t.ticker for t in tiles] == ["MSFT"]
    assert tiles[0].value > 0


def test_fund_list_cli_no_longer_needs_a_sheet(demo_db, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--fund-list"])
    try:
        main.main()
    except SystemExit:
        pass
    assert "Fidelity" in capsys.readouterr().out


def test_removed_sheet_flags_are_gone(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--snapshot-funds-weekly"])
    with pytest.raises(SystemExit):
        main.main()
