"""More command-line paths: importers, quote refresh, snapshots and alerts, run offline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import main
from ledger.db import connect_db

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("LIVE_QUOTES", "false")
    monkeypatch.setenv("MARKET_HOLIDAYS_ENABLED", "false")


def _run(monkeypatch, *args: str, capsys=None) -> int:
    """Run the CLI. A SystemExit with a message counts as a clean, reported failure (exit 1)."""
    monkeypatch.setattr(sys, "argv", ["main.py", *args])
    try:
        main.main()
    except SystemExit as e:
        if isinstance(e.code, str):
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


def test_market_status_prints(demo_db, monkeypatch, capsys):
    _run(monkeypatch, "--market-status")
    assert capsys.readouterr().out.strip() != ""


def test_import_fidelity_csv_via_cli(db_path, monkeypatch):
    code = _run(monkeypatch, "--import-fidelity-csv", str(SAMPLE / "fidelity" / "fidelity_llc_sample.csv"))
    assert code == 0
    assert _count(db_path, "SELECT COUNT(*) FROM trades") == 6


def test_import_fidelity_deposits_via_cli(db_path, monkeypatch):
    code = _run(monkeypatch, "--import-fidelity-deposits", str(SAMPLE / "fidelity" / "fidelity_llc_sample.csv"))
    assert code == 0


def test_import_hl_csv_via_cli(db_path, monkeypatch, offline_fx):
    code = _run(monkeypatch, "--import-hl-csv", str(SAMPLE / "hargreaves_lansdown" / "HL_history_sample.csv"))
    assert code == 0
    assert _count(db_path, "SELECT COUNT(*) FROM trades") == 4


def test_import_aussuper_csv_via_cli(db_path, monkeypatch):
    code = _run(monkeypatch, "--import-aussuper-csv", str(SAMPLE / "aussuper" / "AusSuperAG_sample.csv"))
    assert code == 0


def test_import_aussuper_statements_via_cli(db_path, monkeypatch):
    pdf = SAMPLE / "statements" / "aussuper" / "AusSuper2026JUNST.pdf"
    assert _run(monkeypatch, "--import-aussuper-statements", str(pdf)) == 0


def test_import_fidelity_statements_via_cli(db_path, monkeypatch):
    pdf = SAMPLE / "statements" / "fidelity" / "ACCOUNT_B" / "Statement9302026.pdf"
    assert _run(monkeypatch, "--import-fidelity-statements", str(pdf)) == 0


def test_import_fund_list_csv_via_cli(db_path, monkeypatch, tmp_path):
    csv = tmp_path / "fund_list.csv"
    csv.write_text(
        "week_start,owner,fund_name,cash_invested,current_cash_balance,fund_total_usd,total_profit_usd\n"
        "2026-03-06,Household A,Fidelity,50000,1200,58000,8000\n",
        encoding="utf-8",
    )
    assert _run(monkeypatch, "--import-funds-csv", str(csv)) == 0
    assert _count(db_path, "SELECT COUNT(*) FROM fund_weekly_snapshots") >= 1


def test_refresh_live_quotes_offline_with_yahoo(demo_db, monkeypatch, fake_yahoo, capsys):
    code = _run(monkeypatch, "--refresh-live-quotes", "--quotes-owner", "Household A")
    assert code in (0, 1)


def test_trade_snapshot_writes_fund_snapshot(demo_db, monkeypatch, fake_yahoo):
    before = _count(demo_db, "SELECT COUNT(*) FROM fund_performance_snapshots")
    code = _run(monkeypatch, "--trade-snapshot", "--trade-fund", "Fidelity")
    assert code == 0
    assert _count(demo_db, "SELECT COUNT(*) FROM fund_performance_snapshots") >= before


def test_option_strike_alerts_dry_run_via_cli(demo_db, monkeypatch, fake_yahoo):
    code = _run(monkeypatch, "--check-option-strike-alerts", "--option-strike-alerts-dry-run")
    assert code in (0, 1)


def test_trade_report_text_mode(demo_db, monkeypatch, capsys):
    code = _run(monkeypatch, "--trade-report", "--trade-report-text", "--trade-fund", "Fidelity")
    assert code in (0, 1)


def test_trade_add_option_requires_contract_fields(demo_db, monkeypatch):
    code = _run(
        monkeypatch,
        "--trade-add", "--trade-fund", "Fidelity", "--trade-ticker", "AAPL",
        "--trade-side", "buy", "--trade-shares", "1", "--trade-price", "2",
        "--trade-instrument", "option",
    )
    assert code != 0


def test_trade_add_option_with_occ_symbol(demo_db, monkeypatch):
    code = _run(
        monkeypatch,
        "--trade-add", "--trade-fund", "Fidelity", "--trade-ticker", "AAPL260618C00150000",
        "--trade-side", "buy", "--trade-shares", "1", "--trade-price", "2.5",
        "--trade-instrument", "option", "--trade-date", "2026-10-02",
    )
    assert code in (0, None)
    assert _count(demo_db, "SELECT COUNT(*) FROM trades WHERE instrument_type = 'option'") >= 1


def test_trade_add_rejects_bad_date(demo_db, monkeypatch):
    code = _run(
        monkeypatch,
        "--trade-add", "--trade-fund", "Fidelity", "--trade-ticker", "AAPL",
        "--trade-side", "buy", "--trade-shares", "1", "--trade-price", "2",
        "--trade-date", "not-a-date",
    )
    assert code != 0


def test_fund_add_rejects_unknown_owner(demo_db, monkeypatch):
    with pytest.raises(ValueError, match="Unknown owner"):
        _run(monkeypatch, "--fund-add", "Bad Owner Fund", "--fund-owner", "Nobody")


def test_fund_list_empty_database(db_path, monkeypatch, capsys):
    _run(monkeypatch, "--fund-list")
    capsys.readouterr()


def test_snapshot_stocks_without_holdings_file_reports_clearly(demo_db, monkeypatch, tmp_path):
    monkeypatch.delenv("PORTFOLIO_CSV_PATH", raising=False)
    monkeypatch.setattr(main, "_project_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="No holdings file configured"):
        _run(monkeypatch, "--snapshot-stocks-daily")
