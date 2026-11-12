"""Shared application layer: business operations used by both the web UI and the desktop GUI."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.use_cases import ledger_detail
from config import OWNER_A, OWNER_B
from gui_logic.tables import fund_summary_text, ledger_position_cells


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("LIVE_QUOTES", "false")


def test_ledger_detail_for_a_fund_has_positions_and_one_summary(demo_db):
    detail = ledger_detail(demo_db, owner=OWNER_A, fund="Fidelity", live=False, as_of=date(2026, 11, 10))
    assert detail.fund_filter == "Fidelity"
    assert [f.name for f in detail.funds] == ["Fidelity"]
    assert detail.funds[0].currency == "USD"
    assert {p.ticker for p in detail.positions} >= {"NVDA", "MSFT"}


def test_fund_view_skips_zero_share_positions(demo_db):
    detail = ledger_detail(demo_db, owner=OWNER_A, fund="Fidelity", live=False, as_of=date(2026, 11, 10))
    assert all(p.shares > 0 for p in detail.positions)


def test_unknown_fund_gives_an_empty_detail(demo_db):
    detail = ledger_detail(demo_db, owner=OWNER_A, fund="No Such Fund", live=False)
    assert detail.positions == [] and detail.funds == []


def test_owner_view_lists_each_fund_for_that_owner(demo_db):
    detail = ledger_detail(demo_db, owner=OWNER_B, fund=None, live=False, as_of=date(2026, 11, 10))
    assert [f.name for f in detail.funds] == ["Fidelity Account B"]
    assert detail.fund_filter is None


def test_all_owners_view_lists_every_fund(demo_db):
    detail = ledger_detail(demo_db, owner="All", fund="All", live=False, as_of=date(2026, 11, 10))
    names = {f.name for f in detail.funds}
    assert {"Fidelity", "UK Super", "Super A", "Fidelity Account B"} <= names


def test_owner_with_no_funds_is_empty(demo_db):
    detail = ledger_detail(demo_db, owner="Household C", fund=None, live=False)
    assert detail.funds == []


# --- formatting used by the GUI and the web payload ---------------------------------

def test_position_cells_use_the_position_currency():
    pos = SimpleNamespace(ticker="VAS", shares=5.0, avg_cost=95.0, mark_price=None,
                          market_value=None, unrealized_pl=-2.5, realized_pl=None, currency="AUD")
    cells = ledger_position_cells(pos)
    assert cells == ("VAS", "5", "A$95.00", "—", "—", "A$-2.50", "—")


def test_fund_summary_text_formats_in_fund_currency():
    fund = SimpleNamespace(name="Super A", currency="AUD", cash=100.0, nav=7000.0, roi_pct=3.5)
    assert fund_summary_text(fund) == "Fund “Super A”: cash A$100.00 · NAV A$7,000.00 · ROI +3.50%"
    assert fund_summary_text(fund, " · live marks").endswith("· live marks")


from types import SimpleNamespace as _NS  # noqa: E402
from app.use_cases import heatmap, market_movers  # noqa: E402
from gui_logic.tables import fmt_volume, mover_row_cells  # noqa: E402


def test_heatmap_view_for_all_owners_and_day_colouring(demo_db, fake_yahoo):
    view = heatmap(demo_db, owner="All", fund="All", color_by="day", live=False)
    assert view.color_by == "day"
    assert view.owner is None and view.fund is None
    assert view.total_usd > 0
    assert len(view.tiles) >= 1


def test_heatmap_colour_mode_defaults_to_overall(demo_db, fake_yahoo):
    view = heatmap(demo_db, owner=OWNER_A, fund=None, color_by="unrealised", live=False)
    assert view.color_by == "overall"
    assert view.owner == OWNER_A


def test_market_movers_normalises_the_market_code(fake_yahoo):
    mkt, gainers, losers = market_movers("United States", force=True)
    assert mkt == "US"
    assert gainers[0].symbol == "NVDA" and losers[0].symbol == "XYZ"
    mkt, _g, _l = market_movers("Australia", force=True)
    assert mkt == "AU"


def test_volume_formatting_scales():
    assert fmt_volume(None) == "—"
    assert fmt_volume(999) == "999"
    assert fmt_volume(12_300) == "12.3K"
    assert fmt_volume(4_500_000) == "4.50M"
    assert fmt_volume(2_000_000_000) == "2.00B"


def test_mover_row_cells_formats_each_field():
    row = _NS(rank=1, symbol="NVDA", name="NVIDIA", price=180.0, change=3.0, change_pct=1.7, volume=4_500_000)
    assert mover_row_cells(row, "US") == ("1", "NVDA", "NVIDIA", "$180.00", "$3.00", "+1.70%", "4.50M")
    gap = _NS(rank=2, symbol="X", name="Y", price=None, change=None, change_pct=None, volume=None)
    assert mover_row_cells(gap, "AUS")[3:] == ("—", "—", "—", "—")
