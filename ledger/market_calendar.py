from __future__ import annotations

import os
from datetime import date
from functools import lru_cache


def market_holidays_enabled() -> bool:
    return os.getenv("MARKET_HOLIDAYS_ENABLED", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


@lru_cache(maxsize=1)
def _us_calendar():
    import exchange_calendars as xcals

    return xcals.get_calendar("XNYS")


@lru_cache(maxsize=1)
def _au_calendar():
    import exchange_calendars as xcals

    return xcals.get_calendar("XASX")


def is_us_trading_day(when: date | None = None) -> bool:
    """NYSE session (US public holidays and weekends excluded)."""
    when = when or date.today()
    return bool(_us_calendar().is_session(when))


def is_au_trading_day(when: date | None = None) -> bool:
    """ASX session (Australian public holidays and weekends excluded)."""
    when = when or date.today()
    return bool(_au_calendar().is_session(when))


def any_trading_market_open(when: date | None = None) -> bool:
    """True if either US or Australian equity market has a full session."""
    when = when or date.today()
    return is_us_trading_day(when) or is_au_trading_day(when)


def _is_market_open_now(calendar) -> bool:
    import pandas as pd

    now = pd.Timestamp.now(tz=calendar.tz)
    if not calendar.is_session(now.date()):
        return False
    return bool(calendar.is_open_on_minute(now))


def is_us_market_open_now() -> bool:
    """True during NYSE regular session hours."""
    if not market_holidays_enabled():
        return True
    return _is_market_open_now(_us_calendar())


def is_au_market_open_now() -> bool:
    """True during ASX regular session hours."""
    if not market_holidays_enabled():
        return True
    return _is_market_open_now(_au_calendar())


def any_exchange_open_now() -> bool:
    """True when NYSE or ASX is in its regular intraday session."""
    if not market_holidays_enabled():
        return True
    return is_us_market_open_now() or is_au_market_open_now()


def should_send_holdings_email(when: date | None = None) -> bool:
    """Scheduled holdings email runs only on a US or AU trading day."""
    if not market_holidays_enabled():
        return True
    return any_trading_market_open(when)


def should_poll_live_quotes(when: date | None = None, *, force: bool = False) -> bool:
    """GUI/CLI live Yahoo polls respect the same holiday calendar unless forced."""
    if force or not market_holidays_enabled():
        return True
    return any_trading_market_open(when)


def skip_reason(when: date | None = None) -> str:
    """Human-readable reason when both markets are closed."""
    when = when or date.today()
    us = is_us_trading_day(when)
    au = is_au_trading_day(when)
    if us and au:
        return f"{when.isoformat()}: US and Australian markets open."
    if us:
        return f"{when.isoformat()}: US market open; ASX closed (holiday or weekend)."
    if au:
        return f"{when.isoformat()}: ASX open; US market closed (holiday or weekend)."
    return (
        f"{when.isoformat()}: no NYSE or ASX trading session "
        f"(weekend or public holiday in both markets)."
    )


def exchange_open_reason() -> str:
    """Human-readable intraday status for NYSE and ASX."""
    us_day = is_us_trading_day()
    au_day = is_au_trading_day()
    us_now = is_us_market_open_now()
    au_now = is_au_market_open_now()
    if us_now and au_now:
        return "NYSE and ASX are open."
    if us_now:
        return "NYSE is open; ASX is closed for the day or outside session hours."
    if au_now:
        return "ASX is open; NYSE is closed for the day or outside session hours."
    if us_day and au_day:
        return "NYSE and ASX have sessions today but are outside regular hours."
    if us_day:
        return "NYSE has a session today but is outside regular hours; ASX closed."
    if au_day:
        return "ASX has a session today but is outside regular hours; NYSE closed."
    return "Neither NYSE nor ASX is in a regular session."
