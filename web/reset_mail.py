"""Password-reset email. Uses the same SMTP_* settings as the mailer scripts."""

from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def build_reset_url(token: str) -> str:
    # Token sits in the URL fragment: browsers never send it to the server,
    # so it stays out of access logs and Referer headers.
    base = os.getenv("WEB_PUBLIC_URL", "http://localhost:8080").rstrip("/")
    return f"{base}/#reset={token}"


def send_password_reset_email(*, to: str, username: str, token: str, minutes: int) -> None:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = _parse_int_env("SMTP_PORT", 587)
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_from = os.getenv("EMAIL_FROM", smtp_user or "")

    if not smtp_user or not smtp_password:
        raise ValueError("SMTP_USERNAME and SMTP_PASSWORD are required in .env")
    if not email_from:
        raise ValueError("EMAIL_FROM or SMTP_USERNAME is required in .env")

    url = build_reset_url(token)
    body = (
        f"Hello {username},\n\n"
        "We received a request to reset your Portfolio Manager password.\n\n"
        f"Reset it here (link valid for {minutes} minutes, single use):\n{url}\n\n"
        "If you did not ask for this, ignore this email. Your password stays the same.\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "Reset your Portfolio Manager password"
    msg["From"] = formataddr(("Portfolio Manager", email_from))
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    use_ssl = smtp_port == 465 or os.getenv("SMTP_SSL", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(email_from, [to], msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(email_from, [to], msg.as_string())
