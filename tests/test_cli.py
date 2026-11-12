"""Command-line entry point, run against the demo database with live prices turned off."""

from __future__ import annotations

import sys

import pytest

import main
from ledger.db import connect_db


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("LIVE_QUOTES", "false")
    monkeypatch.setenv("MARKET_HOLIDAYS_ENABLED", "false")


def _run(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["main.py", *args])
    try:
        main.main()
    except SystemExit as e:  # argparse and explicit exits
        if isinstance(e.code, str):  # SystemExit("message") means exit status 1
            print(e.code, file=sys.stderr)
            return 1
        return int(e.code or 0)
    return 0


def _count(db_path, sql: str, *params) -> int:
    conn = connect_db(db_path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def test_fund_list_shows_demo_funds(demo_db, monkeypatch, capsys):
    assert _run(monkeypatch, "--fund-list") in (0, None)
    out = capsys.readouterr().out
    assert "Fidelity" in out
    assert "Fidelity Account B" in out


def test_trade_list_requires_a_fund(demo_db, monkeypatch, capsys):
    code = _run(monkeypatch, "--trade-list")
    assert code != 0


def test_trade_list_prints_fund_trades(demo_db, monkeypatch, capsys):
    _run(monkeypatch, "--trade-list", "--trade-fund", "Fidelity")
    out = capsys.readouterr().out
    assert "NVDA" in out
    assert "MSFT" in out


def test_trade_add_records_a_trade(demo_db, monkeypatch):
    before = _count(demo_db, "SELECT COUNT(*) FROM trades")
    code = _run(
        monkeypatch,
        "--trade-add", "--trade-fund", "Fidelity", "--trade-ticker", "AAPL",
        "--trade-side", "buy", "--trade-shares", "2", "--trade-price", "200",
        "--trade-date", "2026-10-01",
    )
    assert code in (0, None)
    assert _count(demo_db, "SELECT COUNT(*) FROM trades") == before + 1


def test_fund_add_creates_a_fund_for_an_owner(demo_db, monkeypatch):
    code = _run(
        monkeypatch,
        "--fund-add", "Test Fund C", "--fund-owner", "Household C",
        "--fund-cash", "1000", "--fund-currency", "USD",
    )
    assert code in (0, None)
    assert _count(demo_db, "SELECT COUNT(*) FROM trade_funds WHERE name = ? AND owner = ?", "Test Fund C", "Household C") == 1


def test_trade_report_writes_a_pdf(demo_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # the report folder is relative to the working directory
    code = _run(monkeypatch, "--trade-report", "--trade-fund", "Fidelity")
    assert code in (0, None)
    pdfs = list((tmp_path / "reports").glob("trade_Fidelity_*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].read_bytes()[:4] == b"%PDF"


def test_import_trades_csv_round_trip(db_path, monkeypatch, tmp_path):
    csv = tmp_path / "trades.csv"
    csv.write_text(
        "trade_date,fund,ticker,side,shares,price\n"
        "2026-02-02,Import Test,MSFT,buy,3,400\n",
        encoding="utf-8",
    )
    assert _run(monkeypatch, "--import-trades-csv", str(csv)) in (0, None)
    assert _count(db_path, "SELECT COUNT(*) FROM trades") >= 1
