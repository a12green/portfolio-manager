"""Reports, strike-alert email, password-reset email, and the startup admin warning."""

from __future__ import annotations

import getpass
import smtplib
from datetime import date

import pytest

from ledger.db import connect_db
from reports import option_strike_alerts as alerts
from reports.pdf_report import render_pdf
from web import reset_mail, set_password


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("LIVE_QUOTES", "false")
    monkeypatch.setenv("MARKET_HOLIDAYS_ENABLED", "false")


def test_portfolio_pdf_is_rendered_from_demo_data(demo_db, tmp_path):
    out = tmp_path / "portfolio_report.pdf"
    conn = connect_db(demo_db)
    try:
        path = render_pdf(conn, out, generated_on=date(2026, 11, 12))
    finally:
        conn.close()
    assert path == out
    assert out.read_bytes()[:4] == b"%PDF"
    assert out.stat().st_size > 2000


def test_strike_alert_email_is_built_for_no_alerts():
    subject, body = alerts.build_strike_alert_email([], threshold=1.0)
    assert isinstance(subject, str) and subject
    assert isinstance(body, str)


def test_strike_alerts_are_off_unless_enabled(monkeypatch):
    monkeypatch.setenv("OPTION_STRIKE_ALERT_ENABLED", "false")
    assert alerts.strike_alerts_enabled() is False


def test_strike_alert_threshold_defaults_when_missing(monkeypatch):
    monkeypatch.delenv("OPTION_STRIKE_ALERT_THRESHOLD", raising=False)
    assert alerts.strike_alert_threshold() == pytest.approx(1.0)


def test_strike_alert_recipient_is_never_a_built_in_address(monkeypatch):
    monkeypatch.delenv("OPTION_STRIKE_ALERT_TO", raising=False)
    assert alerts.strike_alert_recipient() == ""


def test_reset_url_uses_public_base_and_token(monkeypatch):
    monkeypatch.setenv("WEB_PUBLIC_URL", "https://portfolio.example.com/")
    url = reset_mail.build_reset_url("tok123")
    assert url.startswith("https://portfolio.example.com")
    assert "tok123" in url


class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, *a, **kw):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self, *a, **kw):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)

    def sendmail(self, frm, to, data):
        _FakeSMTP.sent.append((frm, to, data))


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "not-logged-anywhere")
    monkeypatch.setenv("EMAIL_FROM", "sender@example.com")
    monkeypatch.setenv("WEB_PUBLIC_URL", "https://portfolio.example.com")
    return _FakeSMTP


def test_reset_email_contains_link_and_not_the_smtp_password(fake_smtp, caplog):
    reset_mail.send_password_reset_email(
        to="someone@example.com", username="admin", token="tok-abc-123", minutes=30
    )
    assert len(fake_smtp.sent) == 1
    import email

    item = fake_smtp.sent[0]
    raw = item[2] if isinstance(item, tuple) else item
    raw = raw if isinstance(raw, str) else raw.as_string()
    parsed = email.message_from_string(raw)
    body = parsed.get_payload(decode=True).decode("utf-8", "replace")
    assert "tok-abc-123" in body
    assert "not-logged-anywhere" not in raw
    assert "not-logged-anywhere" not in body
    assert "not-logged-anywhere" not in caplog.text


def test_admin_warning_appears_until_password_is_set(demo_db, monkeypatch, capsys):
    import web.__main__ as web_main
    from web import auth

    conn = connect_db(demo_db)
    try:
        auth.init_web_auth_schema(conn)
        auth.seed_default_users(conn)
    finally:
        conn.close()

    web_main._warn_if_admin_locked()
    assert "no password yet" in capsys.readouterr().out

    answers = iter(["admin-passphrase-2", "admin-passphrase-2"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert set_password.main(["admin"]) == 0

    web_main._warn_if_admin_locked()
    assert "no password yet" not in capsys.readouterr().out
