"""Validation for the GUI's entry forms. Raises FormError with a message the user can read."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from gui_logic.formatting import parse_trade_date


class FormError(ValueError):
    """A form value the user must correct. The message is shown as it is."""


def _number(text: str, *, default: float | None = None) -> float:
    raw = (text or "").strip().replace(",", "")
    if not raw:
        if default is None:
            raise FormError("Check shares, price, and fees are numbers.")
        return default
    try:
        return float(raw)
    except ValueError:
        raise FormError("Check shares, price, and fees are numbers.") from None


def parse_optional_amount(raw: str) -> float | None:
    text = (raw or "").strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise FormError(f"{raw.strip()!r} is not a number.") from None


@dataclass(frozen=True)
class TradeForm:
    fund_name: str
    ticker: str
    instrument: str
    side: str
    shares: float
    price: float
    trade_date: date
    fees: float
    notes: str | None
    expiry: date | None
    strike: float | None
    put_call: str | None


def parse_trade_form(
    *,
    fund_name: str,
    ticker: str,
    instrument: str,
    side: str,
    date_text: str,
    shares_text: str,
    price_text: str,
    fees_text: str,
    expiry_text: str,
    strike_text: str,
    put_call_text: str,
    notes_text: str,
) -> TradeForm:
    fund = fund_name.strip()
    if not fund:
        raise FormError("Select or create a fund first.")
    sym = ticker.strip().upper()
    if not sym:
        raise FormError("Enter a ticker.")
    instr = instrument.strip().lower()

    try:
        trade_date = parse_trade_date(date_text)
    except ValueError as e:
        raise FormError(str(e)) from None
    shares = _number(shares_text)
    price = _number(price_text)
    fees = _number(fees_text, default=0.0)
    if shares <= 0 or price < 0 or fees < 0:
        raise FormError("Quantity must be > 0; price and fees must be ≥ 0.")

    expiry_raw = expiry_text.strip()
    try:
        expiry = date.fromisoformat(expiry_raw) if expiry_raw else None
    except ValueError:
        raise FormError("Expiry must be a date like YYYY-MM-DD.") from None
    strike_raw = strike_text.strip().replace(",", "")
    strike = _number(strike_raw) if strike_raw else None
    put_call = put_call_text.strip().lower() or None

    if instr == "option":
        from ledger.option_symbols import is_occ_symbol

        if not is_occ_symbol(sym) and not all([expiry, strike, put_call]):
            raise FormError(
                "Enter an OCC symbol in Ticker, or the underlying symbol plus expiry, strike, and call/put."
            )

    return TradeForm(
        fund_name=fund,
        ticker=sym,
        instrument=instr,
        side=side,
        shares=shares,
        price=price,
        trade_date=trade_date,
        fees=fees,
        notes=notes_text.strip() or None,
        expiry=expiry,
        strike=strike,
        put_call=put_call,
    )


@dataclass(frozen=True)
class FundForm:
    name: str
    owner: str
    opening_cash: float
    currency: str


def parse_fund_form(*, name: str, owner: str, cash_text: str, currency: str) -> FundForm:
    clean = name.strip()
    if not clean:
        raise FormError("Enter a fund name.")
    raw = cash_text.strip().replace(",", "") or "0"
    try:
        cash = float(raw)
    except ValueError:
        raise FormError("Opening cash must be a number.") from None
    return FundForm(name=clean, owner=owner.strip(), opening_cash=cash, currency=currency.strip())


@dataclass(frozen=True)
class BalanceForm:
    fund_name: str
    as_of: date
    nav: float
    cash: float | None
    positions: float | None
    notes: str | None


def parse_balance_form(
    *,
    fund_name: str,
    date_text: str,
    nav_text: str,
    cash_text: str,
    positions_text: str,
    notes_text: str,
) -> BalanceForm:
    name = fund_name.strip()
    if not name:
        raise FormError("Select a fund.")
    try:
        as_of = parse_trade_date(date_text)
    except ValueError as e:
        raise FormError(str(e)) from None
    nav = parse_optional_amount(nav_text)
    if nav is None:
        raise FormError("Enter a total balance (NAV).")
    return BalanceForm(
        fund_name=name,
        as_of=as_of,
        nav=nav,
        cash=parse_optional_amount(cash_text),
        positions=parse_optional_amount(positions_text),
        notes=notes_text.strip() or None,
    )


@dataclass(frozen=True)
class OtherAssetForm:
    owner: str
    asset_type: str
    amount: float
    currency: str
    notes: str | None


def parse_other_asset_form(
    *, owner: str, asset_type: str, amount_text: str, currency_text: str, notes_text: str
) -> OtherAssetForm:
    own = owner.strip()
    kind = asset_type.strip()
    if not own or not kind:
        raise FormError("Select an owner and asset type.")
    amount = parse_optional_amount(amount_text)
    if amount is None:
        raise FormError("Enter an amount.")
    return OtherAssetForm(
        owner=own,
        asset_type=kind,
        amount=amount,
        currency=currency_text.strip() or "USD",
        notes=notes_text.strip() or None,
    )
