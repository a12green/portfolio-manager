"""Filtering, sorting and cells for the GUI tables. Uses plain stand-in rows, no display."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gui_logic.tables import (
    filter_fund_holdings,
    fund_holding_cells,
    fund_holding_rows,
    fund_holdings_summary,
    net_worth_cells,
    net_worth_summary,
    net_worth_total_text,
    sort_fund_holdings,
)


def _row(**kw):
    base = dict(
        owner="Household A", fund="Fidelity", native_currency="USD", ticker="NVDA",
        market="US", shares=10.0, avg_cost=150.0, mark_price=180.0, market_value=1800.0,
        unrealized_pl=300.0, pct_of_fund=44.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


ROWS = [
    _row(ticker="NVDA", market="US", shares=10, unrealized_pl=300.0, pct_of_fund=44.0),
    _row(ticker="VAS", market="AU", shares=5, unrealized_pl=-20.0, pct_of_fund=10.0, owner="Household B", fund="Super A", native_currency="AUD"),
    _row(ticker="AAPL", market="US", shares=3, mark_price=None, market_value=None, unrealized_pl=None, pct_of_fund=None),
]


def test_market_filter_keeps_matching_rows():
    assert [r.ticker for r in filter_fund_holdings(ROWS, "Australia", "")] == ["VAS"]
    assert [r.ticker for r in filter_fund_holdings(ROWS, "United States", "")] == ["NVDA", "AAPL"]


def test_ticker_filter_is_case_insensitive_substring():
    assert [r.ticker for r in filter_fund_holdings(ROWS, "All", " nv ")] == ["NVDA"]


def test_sort_by_shares_descending():
    assert [r.ticker for r in sort_fund_holdings(ROWS, "shares", True)] == ["NVDA", "VAS", "AAPL"]


def test_sort_treats_missing_values_as_zero():
    # Missing P&L sorts as 0, between the loss (-20) and the gain (300).
    assert [r.ticker for r in sort_fund_holdings(ROWS, "unreal", False)] == ["VAS", "AAPL", "NVDA"]


def test_no_sort_keeps_input_order():
    assert [r.ticker for r in sort_fund_holdings(ROWS, None, False)] == ["NVDA", "VAS", "AAPL"]


def test_unknown_sort_column_is_an_error():
    with pytest.raises(KeyError):
        sort_fund_holdings(ROWS, "nonsense", False)


def test_missing_values_show_a_dash():
    cells = fund_holding_cells(ROWS[2])
    assert cells[6] == "—" and cells[7] == "—" and cells[8] == "—" and cells[9] == "—"


def test_cells_format_money_and_percent():
    cells = fund_holding_cells(ROWS[0])
    assert cells[4] == "10"
    assert cells[5] == "$150.00"
    assert cells[9] == "+44.00%"


def test_rows_carry_stripe_and_pl_tags():
    tagged = fund_holding_rows(ROWS, "All", "", None, False)
    tags = [t for t, _ in tagged]
    assert tags[0] == ("avg_good",)
    assert tags[1] == ("stripe", "avg_bad")
    assert tags[2] == ("avg_good",)  # even row, and missing P&L counts as break-even


def test_fund_holdings_summary_counts_holdings_and_funds():
    text = fund_holdings_summary(ROWS, "All funds")
    assert text.startswith("All funds: 3 holdings across 2 fund(s)")


def test_net_worth_cells_and_summary():
    line = SimpleNamespace(label="Fidelity", kind="fund", currency="AUD", amount_local=1000.0,
                           amount_usd=650.0, as_of="2026-10-31", source="manual balance")
    assert net_worth_cells(line)[3] == "A$1,000.00"
    assert net_worth_cells(line)[4] == "$650.00"
    text = net_worth_summary({"AUD": 0.65, "USD": 1.0}, 2, "Household A", use_live=True)
    assert "(live)" in text and "AUD 0.6500" in text and "2 item(s)" in text


def test_net_worth_summary_without_fx_says_usd_only():
    assert "USD only" in net_worth_summary({}, 0, "All owners", use_live=False)


def test_net_worth_total_text():
    assert net_worth_total_text(1234.5, "Household A") == "Total (Household A): $1,234.50"


from gui_logic.tables import balance_row_cells, balances_summary  # noqa: E402


def test_balance_row_with_manual_and_ledger_values():
    cells = balance_row_cells(
        name="Fidelity", owner="Household A", currency="AUD",
        manual_nav=61000.0, manual_cash=3000.0, manual_positions=58000.0,
        manual_date="2026-10-31", ledger_nav=60500.0, ledger_cash=2900.0,
    )
    assert cells == ("Fidelity", "Household A", "AUD", "A$61,000.00", "A$3,000.00",
                     "A$58,000.00", "2026-10-31", "A$60,500.00", "A$2,900.00")


def test_balance_row_without_manual_balance_shows_dashes():
    cells = balance_row_cells(
        name="Fidelity", owner="Household A", currency="USD",
        manual_nav=None, manual_cash=None, manual_positions=None,
        manual_date=None, ledger_nav=None, ledger_cash=None,
    )
    assert cells[3:] == ("—", "—", "—", "—", "—", "—")


def test_balances_summary_text():
    assert balances_summary(4, 1).startswith("4 fund(s) · 1 manual balance(s) on file")


from gui_logic.tables import trade_row_cells  # noqa: E402
from datetime import date as _date  # noqa: E402


def test_trade_row_for_a_stock():
    t = SimpleNamespace(id=7, trade_date=_date(2026, 10, 2), instrument_type="stock", side="buy",
                        ticker="NVDA", is_option=False, shares=10.0, price=180.5, fees=1.0)
    assert trade_row_cells(t) == ("7", "2026-10-02", "stock", "BUY", "NVDA", "10", "180.50", "1.00")


def test_trade_row_for_an_option_uses_readable_label():
    t = SimpleNamespace(id=8, trade_date=_date(2026, 10, 2), instrument_type="option", side="sell",
                        ticker="AAPL270618C00150000", is_option=True, shares=2.0, price=2.5, fees=0.0)
    cells = trade_row_cells(t)
    assert cells[2] == "option" and cells[3] == "SELL"
    assert cells[4] != ""  # the readable label, not an empty cell
