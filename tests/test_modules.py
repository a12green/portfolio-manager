"""Smaller modules: fund-history CSV import, market calendar rules, database setup."""

from __future__ import annotations

from datetime import date

import pytest

from ledger.db import connect_db
from importers.import_funds import import_fund_history_csv
from ledger import market_calendar
from web import auth


def test_fund_history_long_format_imports_rows(db_path, tmp_path):
    csv = tmp_path / "fund_history.csv"
    csv.write_text(
        "week_start,owner,fund_name,cash_invested,current_cash_balance,fund_total_usd,total_profit_usd\n"
        "2026-03-06,Household A,Fidelity,50000,1200,58000,8000\n"
        "2026-03-06,Household B,Fidelity Account B,20000,500,21000,1000\n",
        encoding="utf-8",
    )
    conn = connect_db(db_path)
    try:
        assert import_fund_history_csv(conn, csv) == 2
        rows = conn.execute(
            "SELECT owner, fund_name, fund_total_usd FROM fund_weekly_snapshots ORDER BY owner"
        ).fetchall()
    finally:
        conn.close()
    assert ("Household A", "Fidelity", 58000.0) in [tuple(r) for r in rows]


def test_fund_history_rejects_unknown_owner(db_path, tmp_path):
    csv = tmp_path / "bad_owner.csv"
    csv.write_text(
        "week_start,owner,fund_name,fund_total_usd\n2026-03-06,Stranger,Fidelity,100\n",
        encoding="utf-8",
    )
    conn = connect_db(db_path)
    try:
        with pytest.raises(ValueError, match="Unknown owner"):
            import_fund_history_csv(conn, csv)
    finally:
        conn.close()


def test_fund_history_rejects_missing_week_column(db_path, tmp_path):
    csv = tmp_path / "no_week.csv"
    csv.write_text("owner,fund_name,fund_total_usd\nHousehold A,Fidelity,100\n", encoding="utf-8")
    conn = connect_db(db_path)
    try:
        with pytest.raises(ValueError):
            import_fund_history_csv(conn, csv)
    finally:
        conn.close()


def test_market_holidays_flag_reads_env(monkeypatch):
    monkeypatch.setenv("MARKET_HOLIDAYS_ENABLED", "false")
    assert market_calendar.market_holidays_enabled() is False
    monkeypatch.setenv("MARKET_HOLIDAYS_ENABLED", "true")
    assert market_calendar.market_holidays_enabled() is True


def test_us_christmas_is_not_a_trading_day():
    assert market_calendar.is_us_trading_day(date(2026, 12, 25)) is False


def test_ordinary_weekday_is_a_us_trading_day():
    assert market_calendar.is_us_trading_day(date(2026, 11, 10)) is True


def test_weekend_is_not_a_trading_day():
    assert market_calendar.is_au_trading_day(date(2026, 11, 14)) is False


def test_polling_is_not_skipped_when_holiday_checks_are_off(monkeypatch):
    monkeypatch.setenv("MARKET_HOLIDAYS_ENABLED", "false")
    assert market_calendar.should_poll_live_quotes(date(2026, 12, 25)) is True


def test_init_database_creates_locked_admin(tmp_path, monkeypatch):
    import init_database

    monkeypatch.setattr(init_database, "project_root", tmp_path)
    init_database.init_database()
    db = tmp_path / "data" / "portfolio_history.sqlite3"
    assert db.exists()
    conn = connect_db(db)
    try:
        users = conn.execute("SELECT username, password_hash FROM web_users").fetchall()
    finally:
        conn.close()
    assert [u[0] for u in users] == ["admin"]
    assert not users[0][1]  # locked until a password is set
    check = connect_db(db)
    try:
        assert auth.authenticate_user(check, "admin", "anything-at-all") is None
    finally:
        check.close()
