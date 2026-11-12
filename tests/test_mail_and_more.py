"""Email commands, the PDF report from sheet data, the wide-format fund CSV, and option alerts.

SMTP is replaced by a fake server that records what would have been sent.
"""

from __future__ import annotations

import smtplib
import sys
from datetime import date

import pytest

import main
from config import OWNER_A, OWNER_B
from importers.import_funds import import_fund_history_csv
from ledger.db import connect_db
from ledger.trades_db import init_trade_schema


class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, *args, **kwargs):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self, *args, **kwargs):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, msg, *args, **kwargs):
        _FakeSMTP.sent.append(msg)

    def sendmail(self, frm, to, data, *args, **kwargs):
        _FakeSMTP.sent.append(data)


@pytest.fixture
def smtp(monkeypatch):
    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password-not-real")
    monkeypatch.setenv("EMAIL_FROM", "sender@example.com")
    monkeypatch.setenv("EMAIL_TO", "reader@example.com")
    monkeypatch.setenv("LIVE_QUOTES", "false")
    monkeypatch.setenv("MARKET_HOLIDAYS_ENABLED", "false")
    return _FakeSMTP


def _raw(message) -> str:
    """The sender passes either a message object or the already-built string."""
    return message if isinstance(message, str) else message.as_string()


def _run(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["main.py", *args])
    try:
        main.main()
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 1
        return int(e.code or 0)
    return 0


# --- send_email ------------------------------------------------------------------

def test_send_email_refuses_without_credentials(monkeypatch):
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="SMTP"):
        main.send_email("plain", "<p>html</p>")


def test_send_email_sends_a_message(smtp):
    main.send_email("plain text body", "<p>html body</p>", subject="Test subject")
    assert len(smtp.sent) == 1
    assert "Test subject" in _raw(smtp.sent[0])


def test_send_email_with_attachment(smtp, tmp_path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    main.send_email("plain", "<p>html</p>", attachments=[pdf])
    assert "report.pdf" in _raw(smtp.sent[0])


def test_send_email_uses_ssl_port_465(smtp, monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "465")
    main.send_email("plain", "<p>html</p>")
    assert len(smtp.sent) == 1


# --- CLI email commands -----------------------------------------------------------

def test_email_holdings_from_sheet(demo_db, fake_sheets, smtp, monkeypatch):
    code = _run(monkeypatch, "--email-holdings")
    assert code in (0, None)
    assert len(smtp.sent) == 1


def test_email_trade_report_for_fund(demo_db, smtp, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    code = _run(monkeypatch, "--email-trade-report", "--trade-fund", "Fidelity")
    assert code in (0, None)
    assert len(smtp.sent) == 1


def test_pdf_report_from_demo_and_sheet(demo_db, fake_sheets, monkeypatch, tmp_path):
    out = tmp_path / "portfolio_report.pdf"
    monkeypatch.setenv("PDF_OUTPUT_PATH", str(out))
    code = _run(monkeypatch, "--pdf-report")
    assert code in (0, None)
    assert out.exists() and out.read_bytes()[:4] == b"%PDF"


# --- wide-format fund CSV ---------------------------------------------------------

def test_wide_format_fund_csv_imports_each_owner(db_path, tmp_path):
    csv = tmp_path / "wide.csv"
    csv.write_text(
        "week_start,owner_a_fund,owner_a_b,owner_a_d,owner_a_i,owner_a_k,"
        "owner_b_fund,owner_b_b,owner_b_i,owner_b_k\n"
        "2026-03-06,Fidelity,50000,1200,58000,8000,Fidelity Account B,20000,21000,1000\n",
        encoding="utf-8",
    )
    conn = connect_db(db_path)
    try:
        count = import_fund_history_csv(conn, csv)
        owners = sorted(r[0] for r in conn.execute("SELECT DISTINCT owner FROM fund_weekly_snapshots"))
    finally:
        conn.close()
    assert count == 2
    assert owners == sorted([OWNER_A, OWNER_B])


def test_wide_format_requires_week_column(db_path, tmp_path):
    csv = tmp_path / "bad_wide.csv"
    csv.write_text("owner_a_fund,owner_a_i\nFidelity,100\n", encoding="utf-8")
    conn = connect_db(db_path)
    try:
        with pytest.raises(ValueError, match="week_start"):
            import_fund_history_csv(conn, csv)
    finally:
        conn.close()


# --- option strike alerts -----------------------------------------------------------

def test_option_strike_alert_detected_and_emailed(demo_db, fake_yahoo, smtp, monkeypatch):
    # A call struck at 100 with the underlying around 101.5 is within 2.0 of strike.
    # Alerts need live prices, so live quotes are switched on here (the fake supplies them).
    monkeypatch.setenv("LIVE_QUOTES", "true")
    monkeypatch.setenv("OPTION_STRIKE_ALERT_ENABLED", "true")
    monkeypatch.setenv("OPTION_STRIKE_THRESHOLD", "2.0")
    monkeypatch.setenv("OPTION_STRIKE_ALERT_TO", "reader@example.com")
    code = _run(
        monkeypatch,
        "--trade-add", "--trade-fund", "Fidelity", "--trade-ticker", "AAPL270618C00100000",
        "--trade-side", "buy", "--trade-shares", "1", "--trade-price", "2.5",
        "--trade-instrument", "option", "--trade-date", "2026-10-02",
    )
    assert code in (0, None)
    # Expired contracts are skipped, so this one expires in 2027.
    code = _run(monkeypatch, "--check-option-strike-alerts", "--option-strike-alerts-force")
    assert code in (0, None)
    assert len(_FakeSMTP.sent) == 1
    assert "Option near strike alert" in _raw(_FakeSMTP.sent[0])


def test_option_strike_alert_dry_run_sends_nothing(demo_db, fake_yahoo, smtp, monkeypatch):
    monkeypatch.setenv("OPTION_STRIKE_ALERT_ENABLED", "true")
    monkeypatch.setenv("OPTION_STRIKE_ALERT_TO", "reader@example.com")
    _run(monkeypatch, "--check-option-strike-alerts", "--option-strike-alerts-dry-run", "--option-strike-alerts-force")
    assert _FakeSMTP.sent == []


def test_option_strike_alerts_disabled_by_env(demo_db, fake_yahoo, smtp, monkeypatch):
    monkeypatch.setenv("OPTION_STRIKE_ALERT_ENABLED", "false")
    _run(monkeypatch, "--check-option-strike-alerts", "--option-strike-alerts-force")
    assert _FakeSMTP.sent == []
