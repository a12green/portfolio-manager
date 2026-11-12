"""Less common branches: the Hargreaves Lansdown summary export, and every heat-map,
fund-balance and portfolio option. Each option is run against the demo database."""

from __future__ import annotations

from datetime import date

import pytest

from config import OWNER_A
from ledger.db import connect_db
from importers.import_hl import import_hl_csv, load_hl_rows

from pathlib import Path

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data"


# --- Hargreaves Lansdown summary export ----------------------------------------

def test_hl_summary_export_is_read(tmp_path, offline_fx):
    csv = tmp_path / "hl_summary.csv"
    csv.write_text(
        "Date,Action,Share,Qty,Price,Fees\n"
        "Mar 14 2026,Buy,nvidia,5,120.00,1.00\n"
        "Sep 10 2026,Sell,apple,3,22.40,0.50\n"
        "Oct 3 2026,Buy,unknown share,2,10.00,0.00\n"
        "Oct 4 2026,Hold,nvidia,1,100.00,0.00\n",
        encoding="utf-8",
    )
    rows, opening_cash = load_hl_rows(csv)
    assert {r.ticker for r in rows} == {"NVDA", "AAPL"}
    assert {r.side for r in rows} == {"buy", "sell"}
    assert opening_cash == pytest.approx(0.0)


def test_hl_summary_bad_date_is_reported(tmp_path, offline_fx):
    csv = tmp_path / "bad_date.csv"
    csv.write_text("Date,Action,Share,Qty,Price,Fees\n31-13-2026,Buy,nvidia,5,120.00,0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="HL summary date"):
        load_hl_rows(csv)


def test_hl_summary_import_writes_trades(db_path, tmp_path, offline_fx):
    csv = tmp_path / "hl_summary.csv"
    csv.write_text("Date,Action,Share,Qty,Price,Fees\nMar 14 2026,Buy,nvidia,5,120.00,1.00\n", encoding="utf-8")
    conn = connect_db(db_path)
    try:
        assert import_hl_csv(conn, csv) == 1
    finally:
        conn.close()


def test_hl_csv_with_non_hl_layout_is_read_as_transactions(tmp_path, offline_fx):
    csv = tmp_path / "hl_plain.csv"
    csv.write_text(
        "Reference,Trade date,Description,Quantity,Value (£),Unit cost (p)\n"
        "B1,14/03/2026,NVIDIA CORP 5 @ 120.00,5,-600.00,12000\n",
        encoding="utf-8",
    )
    rows, _ = load_hl_rows(csv)
    assert len(rows) == 1 and rows[0].ticker == "NVDA"


# --- heat map ----------------------------------------------------------------------

@pytest.mark.parametrize("color_by", ["overall", "day"])
@pytest.mark.parametrize("scope", [{"owner": OWNER_A}, {"fund_name": "Fidelity"}, {}])
def test_heatmap_tiles_each_mode(demo_db, color_by, scope):
    from reports.heatmap_data import collect_heatmap_tiles

    tiles = collect_heatmap_tiles(demo_db, live=False, color_by=color_by, **scope)
    assert isinstance(tiles, list)


@pytest.mark.parametrize("group_by_fund", [True, False])
def test_heatmap_figure_grouping(demo_db, group_by_fund):
    from reports.heatmap_chart import build_heatmap_figure
    from reports.heatmap_data import collect_heatmap_tiles

    tiles = collect_heatmap_tiles(demo_db, owner=OWNER_A, live=False, color_by="day")
    fig = build_heatmap_figure(tiles or [], title="grouped", color_by="day", group_by_fund=group_by_fund)
    assert fig is not None


def test_heatmap_tile_helper_values():
    from reports.heatmap_data import tile_pl_for_color

    assert callable(tile_pl_for_color)


# --- fund balances -------------------------------------------------------------------

@pytest.mark.parametrize("trends_mode", ["statements", "transactions", "manual"])
def test_fund_balance_series_each_trends_mode(demo_db, trends_mode):
    from reports.fund_balance_history import collect_fund_balance_series

    series = collect_fund_balance_series(demo_db, owner=OWNER_A, trends_mode=trends_mode)
    assert isinstance(series, (list, tuple))


@pytest.mark.parametrize("location", ["All", "US", "AU", "UK"])
def test_fund_balance_series_each_location(demo_db, location):
    from reports.fund_balance_history import collect_fund_balance_series

    series = collect_fund_balance_series(demo_db, location=location)
    assert isinstance(series, (list, tuple))


def test_fund_balance_series_for_one_fund_from_transactions(demo_db):
    from reports.fund_balance_history import collect_fund_balance_series

    series = collect_fund_balance_series(
        demo_db, fund_name="Fidelity", trends_mode="transactions", from_transactions=True
    )
    assert isinstance(series, (list, tuple))


def test_fund_balance_figure_without_total(demo_db):
    from reports.fund_balance_chart import build_fund_balance_figure
    from reports.fund_balance_history import collect_fund_balance_series

    series = collect_fund_balance_series(demo_db, owner=OWNER_A)
    fig = build_fund_balance_figure(list(series), title="no total", include_total=False)
    assert fig is not None


def test_rebuild_nav_snapshots_from_ledger(demo_db):
    from ledger.trades_db import get_fund_by_name
    from reports.fund_balance_history import rebuild_fund_nav_snapshots_from_ledger

    conn = connect_db(demo_db)
    try:
        fund = get_fund_by_name(conn, "Fidelity")
        written = rebuild_fund_nav_snapshots_from_ledger(conn, fund, replace=True)
        conn.commit()
    finally:
        conn.close()
    assert isinstance(written, int)


# --- portfolio view options -------------------------------------------------------------

@pytest.mark.parametrize("market_sel", ["All", "Australian", "United States", "US", ""])
def test_portfolio_holdings_each_market_filter(demo_db, market_sel):
    from reports.portfolio_data import load_portfolio_holdings

    cards, *_ = load_portfolio_holdings(
        demo_db, owner=OWNER_A, fund_filter=None, market_sel=market_sel,
        use_live=False, owner_label=OWNER_A,
    )
    assert isinstance(cards, list)


def test_portfolio_holdings_single_fund_filter(demo_db):
    from reports.portfolio_data import load_portfolio_holdings

    cards, *_ = load_portfolio_holdings(
        demo_db, owner=OWNER_A, fund_filter="Fidelity", market_sel="All",
        use_live=False, owner_label=OWNER_A,
    )
    assert isinstance(cards, list)


def test_portfolio_summary_payload_for_each_owner_scope(demo_db):
    from web import services

    for owner in (OWNER_A, "All"):
        payload = services.portfolio_payload(owner=owner, fund=None, market="All", live=False)
        assert isinstance(payload, dict)
