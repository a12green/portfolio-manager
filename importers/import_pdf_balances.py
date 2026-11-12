"""Import Portfolio Balances PDF into a chosen fund's manual balance."""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ledger.money import parse_money_scalar
from ledger.trades_db import (
    get_fund_by_name,
    init_trade_schema,
    upsert_fund_manual_balance,
    upsert_fund_reported_cash,
)

try:
    import fitz  # pymupdf
except ImportError:  # pragma: no cover
    fitz = None

_ACCOUNT_RE = re.compile(r"\b(Z\d{8}|\d{9})\b")
_AS_OF_RE = re.compile(
    r"As of\s+([A-Za-z]{3})-(\d{1,2})-(\d{4})\b",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(r"-?\$[\d,]+\.\d{2}")


@dataclass(frozen=True)
class ParsedPortfolioBalances:
    account_id: str | None
    fund_name: str
    balance_date: date
    nav: float
    cash_balance: float
    cash_market_value: float | None
    margin_market_value: float | None
    positions_value: float
    source_file: str


def _money_after_label(text: str, label: str) -> float | None:
    """First money amount on the line after ``label`` (Fidelity balances layout)."""
    pattern = re.compile(
        rf"{re.escape(label)}\s*\n\s*({_MONEY_RE.pattern})",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None
    return float(parse_money_scalar(m.group(1)))


def _parse_as_of_date(text: str) -> date:
    m = _AS_OF_RE.search(text)
    if m:
        mon, day, year = m.groups()
        try:
            return datetime.strptime(f"{mon}-{int(day):02d}-{year}", "%b-%d-%Y").date()
        except ValueError:
            pass
    # Fallback: print timestamp in footer "7/24/26, 12:26 PM"
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2}),\s*\d", text)
    if m:
        month, day, yy = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        year = 2000 + yy if yy < 100 else yy
        return date(year, month, day)
    return date.today()


def _extract_account_id(text: str, path: Path) -> str | None:
    m = _ACCOUNT_RE.search(text)
    if m:
        return m.group(1)
    m = _ACCOUNT_RE.search(path.stem.upper())
    return m.group(1) if m else None


def _extract_nav(text: str, account_id: str | None) -> float:
    if account_id:
        m = re.search(
            rf"{re.escape(account_id)}\s*\n\s*({_MONEY_RE.pattern})",
            text,
            re.IGNORECASE,
        )
        if m:
            return float(parse_money_scalar(m.group(1)))
    # Prefer the account Balance heading over other "$" figures on the page.
    m = re.search(
        rf"\bBalance\b\s*\n(?:Day change\s*\n)?\s*({_MONEY_RE.pattern})",
        text,
        re.IGNORECASE,
    )
    if m:
        return float(parse_money_scalar(m.group(1)))
    nav = _money_after_label(text, "Balance")
    if nav is not None and nav > 0:
        return nav
    raise ValueError("Could not parse account balance (NAV) from PDF")


def parse_portfolio_balances_pdf(
    path: Path,
    *,
    fund_name: str,
) -> ParsedPortfolioBalances:
    """Parse a Fidelity digital Portfolio Balances PDF for ``fund_name``."""
    if fitz is None:
        raise ImportError("pymupdf is required (pip install pymupdf)")
    name = str(fund_name or "").strip()
    if not name:
        raise ValueError("fund_name is required")

    path = path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Portfolio balances PDF not found: {path.resolve()}")

    doc = fitz.open(path)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()

    account_id = _extract_account_id(text, path)
    balance_date = _parse_as_of_date(text)
    nav = _extract_nav(text, account_id)

    net_debit = _money_after_label(text, "Net debit")
    cash_mv = _money_after_label(text, "Cash market value")
    margin_mv = _money_after_label(text, "Margin market value")
    settled = _money_after_label(text, "Settled cash")

    # Prefer net debit (margin loan) when present; else settled cash.
    if net_debit is not None:
        cash_balance = float(net_debit)
    elif settled is not None:
        cash_balance = float(settled)
    else:
        cash_balance = 0.0

    if cash_mv is not None and margin_mv is not None:
        positions_value = float(cash_mv) + float(margin_mv)
    else:
        positions_value = float(nav) - cash_balance

    return ParsedPortfolioBalances(
        account_id=account_id,
        fund_name=name,
        balance_date=balance_date,
        nav=float(nav),
        cash_balance=cash_balance,
        cash_market_value=cash_mv,
        margin_market_value=margin_mv,
        positions_value=positions_value,
        source_file=path.name,
    )


def import_portfolio_balances_pdf(
    conn: sqlite3.Connection,
    path: Path,
    *,
    fund_name: str,
) -> ParsedPortfolioBalances:
    """Parse PDF and store as the given fund's manual balance (+ reported cash)."""
    init_trade_schema(conn)
    parsed = parse_portfolio_balances_pdf(path, fund_name=fund_name)
    fund = get_fund_by_name(conn, parsed.fund_name)
    if fund is None:
        raise ValueError(
            f"Fund {parsed.fund_name!r} not found. Create it before importing balances."
        )

    acct = parsed.account_id or "account"
    notes = (
        f"{parsed.source_file} · {acct} as of {parsed.balance_date.isoformat()}"
    )
    upsert_fund_manual_balance(
        conn,
        fund.id,
        parsed.balance_date,
        parsed.nav,
        cash_balance=parsed.cash_balance,
        positions_value=parsed.positions_value,
        notes=notes,
    )
    upsert_fund_reported_cash(
        conn,
        fund.id,
        parsed.balance_date,
        parsed.cash_balance,
        source=parsed.source_file,
    )
    return parsed


def run_import_portfolio_balances(
    path: Path,
    *,
    fund_name: str,
    dry_run: bool = False,
) -> ParsedPortfolioBalances:
    import trades_cli
    from ledger.db import connect_db

    if dry_run:
        return parse_portfolio_balances_pdf(path, fund_name=fund_name)

    conn = connect_db(trades_cli._db_path())
    try:
        return import_portfolio_balances_pdf(conn, path, fund_name=fund_name)
    finally:
        conn.close()


def _print_result(parsed: ParsedPortfolioBalances, *, dry_run: bool) -> None:
    action = "Would save" if dry_run else "Saved"
    acct = parsed.account_id or "—"
    print(
        f"{action} {parsed.fund_name} balance from {parsed.source_file}:\n"
        f"  Account:    {acct}\n"
        f"  As of:      {parsed.balance_date.isoformat()}\n"
        f"  NAV:        ${parsed.nav:,.2f}\n"
        f"  Cash/debit: ${parsed.cash_balance:,.2f}\n"
        f"  Positions:  ${parsed.positions_value:,.2f}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import a Portfolio Balances PDF into a fund's manual balance. "
            "The target fund is required (--fund)."
        ),
    )
    parser.add_argument(
        "pdf",
        type=Path,
        help="Path to Portfolio Balances.pdf (broker digital balances export).",
    )
    parser.add_argument(
        "--fund",
        required=True,
        help="Fund name to update (e.g. Fidelity, Fidelity HSA, Fidelity Account B).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print values without writing to the database.",
    )
    args = parser.parse_args(argv)

    parsed = run_import_portfolio_balances(
        args.pdf,
        fund_name=args.fund,
        dry_run=args.dry_run,
    )
    _print_result(parsed, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
