"""Logic behind the desktop GUI: form validation and display formatting. No display needed."""

from __future__ import annotations

from datetime import date

import pytest

from gui_logic import formatting as fmt
from gui_logic.forms import (
    FormError,
    parse_balance_form,
    parse_fund_form,
    parse_optional_amount,
    parse_other_asset_form,
    parse_trade_form,
)


def _trade(**overrides):
    base = dict(
        fund_name="Fidelity", ticker="nvda", instrument="stock", side="buy",
        date_text="2026-10-02", shares_text="10", price_text="180.5", fees_text="1",
        expiry_text="", strike_text="", put_call_text="call", notes_text="  note  ",
    )
    base.update(overrides)
    return parse_trade_form(**base)


# --- trade form -----------------------------------------------------------------

def test_valid_stock_trade_is_normalised():
    form = _trade()
    assert form.ticker == "NVDA"
    assert form.shares == 10 and form.price == pytest.approx(180.5)
    assert form.trade_date == date(2026, 10, 2)
    assert form.notes == "note"
    assert form.expiry is None


def test_blank_date_means_today():
    assert _trade(date_text="").trade_date == date.today()


def test_blank_notes_become_none():
    assert _trade(notes_text="   ").notes is None


@pytest.mark.parametrize("field, value, message", [
    ("fund_name", "  ", "Select or create a fund first."),
    ("ticker", "", "Enter a ticker."),
    ("date_text", "2026/10/02/x", "Use a date like YYYY-MM-DD"),
    ("shares_text", "ten", "Check shares, price, and fees are numbers."),
    ("price_text", "", "Check shares, price, and fees are numbers."),
    ("fees_text", "abc", "Check shares, price, and fees are numbers."),
    ("shares_text", "0", "Quantity must be > 0"),
    ("price_text", "-1", "price and fees must be"),
    ("fees_text", "-0.5", "price and fees must be"),
    ("expiry_text", "31/12/2026", "Expiry must be a date like YYYY-MM-DD."),
])
def test_invalid_trade_values_are_reported(field, value, message):
    with pytest.raises(FormError, match=message):
        _trade(**{field: value})


def test_blank_fees_default_to_zero():
    assert _trade(fees_text="").fees == 0.0


def test_thousands_separators_are_accepted():
    assert _trade(shares_text="1,250").shares == 1250


def test_option_needs_occ_symbol_or_full_contract_details():
    with pytest.raises(FormError, match="OCC symbol"):
        _trade(instrument="option", ticker="AAPL", expiry_text="", strike_text="", put_call_text="call")


def test_option_with_full_details_is_accepted():
    form = _trade(instrument="option", ticker="AAPL", expiry_text="2027-06-18", strike_text="150", put_call_text="Call")
    assert form.expiry == date(2027, 6, 18)
    assert form.strike == 150.0
    assert form.put_call == "call"


def test_option_with_occ_symbol_needs_no_extra_fields():
    form = _trade(instrument="option", ticker="AAPL270618C00150000", expiry_text="", strike_text="")
    assert form.ticker == "AAPL270618C00150000"


# --- fund form -------------------------------------------------------------------

def test_fund_form_defaults_cash_to_zero():
    form = parse_fund_form(name=" New Fund ", owner="Household A", cash_text="", currency="usd")
    assert form.name == "New Fund"
    assert form.opening_cash == 0.0
    assert form.currency == "usd"


def test_fund_form_needs_a_name():
    with pytest.raises(FormError, match="Enter a fund name."):
        parse_fund_form(name="   ", owner="Household A", cash_text="0", currency="USD")


def test_fund_form_rejects_non_numeric_cash():
    with pytest.raises(FormError, match="Opening cash must be a number."):
        parse_fund_form(name="X", owner="Household A", cash_text="lots", currency="USD")


# --- balance and other asset forms ---------------------------------------------------

def test_balance_form_parses_optional_fields():
    form = parse_balance_form(
        fund_name="Fidelity", date_text="2026-10-31", nav_text="$61,000",
        cash_text="", positions_text="1,000.5", notes_text="",
    )
    assert form.nav == 61000.0
    assert form.cash is None
    assert form.positions == pytest.approx(1000.5)
    assert form.as_of == date(2026, 10, 31)


def test_balance_form_requires_nav_and_fund():
    with pytest.raises(FormError, match="Select a fund."):
        parse_balance_form(fund_name="", date_text="", nav_text="1", cash_text="", positions_text="", notes_text="")
    with pytest.raises(FormError, match="Enter a total balance"):
        parse_balance_form(fund_name="F", date_text="", nav_text="", cash_text="", positions_text="", notes_text="")


def test_optional_amount_rules():
    assert parse_optional_amount("") is None
    assert parse_optional_amount("$1,234.50") == pytest.approx(1234.5)
    with pytest.raises(FormError, match="is not a number"):
        parse_optional_amount("12x")


def test_other_asset_form_defaults_currency():
    form = parse_other_asset_form(owner="Household A", asset_type="Term deposit", amount_text="500",
                                  currency_text="", notes_text="")
    assert form.currency == "USD"
    assert form.amount == 500.0


def test_other_asset_form_requires_owner_type_and_amount():
    with pytest.raises(FormError, match="Select an owner and asset type."):
        parse_other_asset_form(owner="", asset_type="x", amount_text="1", currency_text="", notes_text="")
    with pytest.raises(FormError, match="Enter an amount."):
        parse_other_asset_form(owner="A", asset_type="x", amount_text="", currency_text="", notes_text="")


# --- formatting ---------------------------------------------------------------------

@pytest.mark.parametrize("amount, loc, expected", [
    (1234.5, "", "$1,234.50"),
    (1234.5, "aus", "A$1,234.50"),
])
def test_money_formatting(amount, loc, expected):
    assert fmt.fmt_money(amount, loc) == expected


def test_percentage_formatting_shows_sign():
    assert fmt.fmt_pct(2.5) == "+2.50%"
    assert fmt.fmt_pct(-1.0) == "-1.00%"
    assert fmt.fmt_pct(0.0) == "0.00%"


def test_fund_money_uses_fund_currency():
    assert fmt.fmt_fund_money(None, "USD") == "—"
    assert fmt.fmt_fund_money(10.0, "AUD") == "A$10.00"
    assert fmt.fmt_fund_money(10.0, "GBP") == "$10.00"


def test_currency_location_mapping():
    assert fmt.currency_loc("aud") == "AUS"
    assert fmt.currency_loc("GBP") == "UK"
    assert fmt.currency_loc("") == "US"


def test_holding_market_detection():
    assert fmt.holding_market("AUS", "VAS") == "AU"
    assert fmt.holding_market("", "ASX:BHP") == "AU"
    assert fmt.holding_market("", "NVDA") == "US"


def test_market_filter_matches_selection():
    assert fmt.matches_port_market("AUS", "VAS", "Australia")
    assert not fmt.matches_port_market("AUS", "VAS", "United States")
    assert fmt.matches_port_market("", "NVDA", "All")


def test_row_tags_stripe_odd_rows_only():
    assert fmt.tree_row_tags(0) == ()
    assert fmt.tree_row_tags(1, "avg_good") == ("stripe", "avg_good")


def test_pl_tag_sign():
    assert fmt.pl_row_tag(None) == "avg_good"
    assert fmt.pl_row_tag(-0.01) == "avg_bad"


def test_owner_label_maps_all_and_display_names():
    display = {"Household A": "Household A"}
    assert fmt.port_owner_label("all", display) == "All owners"
    assert fmt.port_owner_label("Household A", display) == "Household A"
