"""Email when an option underlying first trades within $1 of strike."""

from __future__ import annotations

import html as html_module
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ledger.db import connect_db
from reports.holdings_compare import LedgerCardPosition, collect_ledger_card_positions
from ledger.option_symbols import display_option_label, parse_occ_symbol
from ledger.quotes import fetch_live_quotes, listing_currency, normalize_ticker
from ledger.trades_db import init_trade_schema, mark_option_strike_alerted, fetch_option_strike_alerted


@dataclass(frozen=True)
class StrikeAlert:
    occ_symbol: str
    label: str
    underlying: str
    strike: float
    put_call: str | None
    expiry: date
    underlying_price: float
    distance: float
    contracts: float


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def strike_alerts_enabled() -> bool:
    return _env_bool("OPTION_STRIKE_ALERT_ENABLED", default=True)


def strike_alert_threshold() -> float:
    try:
        return float(os.getenv("OPTION_STRIKE_THRESHOLD", "1.0"))
    except ValueError:
        return 1.0


def strike_alert_recipient() -> str:
    return os.getenv("OPTION_STRIKE_ALERT_TO", "").strip()


def strike_alert_subject() -> str:
    return os.getenv("OPTION_STRIKE_ALERT_SUBJECT", "Option near strike alert").strip()


def _resolve_contract(pos: LedgerCardPosition) -> tuple[str, float, str] | None:
    """Return (underlying, strike, occ_symbol) or None if incomplete."""
    parsed = parse_occ_symbol(pos.ticker)
    underlying = parsed.underlying if parsed else (pos.underlying or "").strip().upper()
    strike = pos.strike if pos.strike is not None else (parsed.strike if parsed else None)
    occ = parsed.occ_symbol if parsed else pos.ticker.strip().upper()
    if not underlying or strike is None or not occ:
        return None
    return underlying, float(strike), occ


def _open_option_positions(conn) -> list[LedgerCardPosition]:
    today = date.today()
    positions = collect_ledger_card_positions(
        conn, owner="All", live=True, force_live=True
    )
    out: list[LedgerCardPosition] = []
    for pos in positions:
        if pos.instrument_type != "option" or pos.shares <= 0:
            continue
        if pos.expiry and pos.expiry < today:
            continue
        if _resolve_contract(pos) is None:
            continue
        out.append(pos)
    return out


def find_new_strike_alerts(
    conn,
    *,
    threshold: float | None = None,
) -> list[StrikeAlert]:
    threshold = strike_alert_threshold() if threshold is None else threshold
    already = fetch_option_strike_alerted(conn)
    positions = _open_option_positions(conn)
    if not positions:
        return []

    quote_symbols: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pos in positions:
        resolved = _resolve_contract(pos)
        if not resolved:
            continue
        underlying, _strike, occ = resolved
        if occ in already:
            continue
        key = (
            underlying,
            listing_currency(underlying, market=pos.market, fund_currency=pos.currency),
        )
        if key in seen:
            continue
        seen.add(key)
        quote_symbols.append(key)

    if not quote_symbols:
        return []

    prices = fetch_live_quotes(quote_symbols)
    alerts: list[StrikeAlert] = []
    for pos in positions:
        resolved = _resolve_contract(pos)
        if not resolved:
            continue
        underlying, strike, occ = resolved
        if occ in already:
            continue
        und_norm = normalize_ticker(underlying)
        price = prices.get(und_norm)
        if price is None:
            continue
        distance = abs(float(price) - strike)
        if distance > threshold:
            continue
        label = pos.display_name or display_option_label(
            underlying=underlying,
            expiry=pos.expiry,
            strike=strike,
            put_call=pos.put_call,
            occ_symbol=occ,
        )
        alerts.append(
            StrikeAlert(
                occ_symbol=occ,
                label=label,
                underlying=underlying,
                strike=strike,
                put_call=pos.put_call,
                expiry=pos.expiry or date.today(),
                underlying_price=float(price),
                distance=distance,
                contracts=pos.shares,
            )
        )
    return alerts


def build_strike_alert_email(alerts: list[StrikeAlert], *, threshold: float) -> tuple[str, str]:
    lines = [
        "An option underlying has moved within "
        f"${threshold:g} of strike for the first time.",
        "",
    ]
    html_rows = []
    for a in alerts:
        cp = (a.put_call or "").capitalize()
        lines.append(
            f"{a.label}: {a.underlying} @ ${a.underlying_price:.2f} "
            f"(strike ${a.strike:.2f}, ${a.distance:.2f} away, {a.contracts:g} contracts)"
        )
        html_rows.append(
            "<tr>"
            f"<td>{html_module.escape(a.label)}</td>"
            f"<td>{html_module.escape(a.underlying)}</td>"
            f"<td>${a.strike:.2f}</td>"
            f"<td>${a.underlying_price:.2f}</td>"
            f"<td>${a.distance:.2f}</td>"
            f"<td>{html_module.escape(cp)}</td>"
            f"<td>{a.contracts:g}</td>"
            "</tr>"
        )
    plain = "\n".join(lines) + "\n"
    html = (
        f"<p>An option underlying has moved within <b>${threshold:g}</b> of strike "
        "for the first time.</p>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr>"
        "<th>Contract</th><th>Underlying</th><th>Strike</th>"
        "<th>Stock</th><th>Distance</th><th>Type</th><th>Contracts</th>"
        "</tr></thead><tbody>"
        + "".join(html_rows)
        + "</tbody></table>"
    )
    return plain, html


def run_option_strike_alerts(
    db_path: Path | None = None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> list[StrikeAlert]:
    """Check open options and email on first near-strike crossing."""
    if not strike_alerts_enabled() and not dry_run:
        print("Option strike alerts disabled (OPTION_STRIKE_ALERT_ENABLED=false).")
        return []

    from ledger.market_calendar import any_exchange_open_now, exchange_open_reason

    if not force and not dry_run:
        from ledger.market_calendar import market_holidays_enabled

        if market_holidays_enabled() and not any_exchange_open_now():
            print(f"Option strike alerts skipped: {exchange_open_reason()}")
            return []

    import trades_cli

    path = db_path or trades_cli._db_path()
    threshold = strike_alert_threshold()
    conn = connect_db(path)
    try:
        init_trade_schema(conn)
        alerts = find_new_strike_alerts(conn, threshold=threshold)
        if not alerts:
            print("No new near-strike option alerts.")
            return []

        if dry_run:
            for a in alerts:
                print(
                    f"[dry-run] {a.label}: {a.underlying} ${a.underlying_price:.2f} "
                    f"vs strike ${a.strike:.2f} (${a.distance:.2f} away)"
                )
            return alerts

        recorded: list[StrikeAlert] = []
        for a in alerts:
            if mark_option_strike_alerted(
                conn,
                occ_symbol=a.occ_symbol,
                underlying=a.underlying,
                strike=a.strike,
                expiry=a.expiry,
                put_call=a.put_call,
                underlying_price=a.underlying_price,
                distance=a.distance,
            ):
                recorded.append(a)

        if not recorded:
            print("Near-strike alerts already recorded; no email sent.")
            return []

        from main import send_email

        plain, html = build_strike_alert_email(recorded, threshold=threshold)
        send_email(
            plain,
            html,
            to=strike_alert_recipient(),
            subject=strike_alert_subject(),
        )
        print(
            f"Option strike alert email sent to {strike_alert_recipient()} "
            f"({len(recorded)} contract(s))."
        )
        return recorded
    finally:
        conn.close()
