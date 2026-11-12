from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch

from ledger.trade_ledger import FundLedgerResult
from reports.trade_report import _fmt_money, _fmt_pct, _fmt_shares

_NAVY = "#1e3a5f"
_SLATE = "#4a5568"
_MUTED = "#718096"
_POS = "#1b7a3d"
_NEG = "#b91c1c"
_STRIPE = "#f7fafc"
_WHITE = "#ffffff"
_ACCENT = "#e8eef5"


def _apply_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
            "font.size": 9,
        }
    )


def _currency_prefix(currency: str) -> str:
    return "A$" if currency == "AUD" else "£" if currency == "GBP" else "$"


def _parse_money_cell(text: str) -> float | None:
    if not text or text == "—":
        return None
    cleaned = text.replace(",", "").replace("A$", "").replace("$", "").replace("£", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_pct_cell(text: str) -> float | None:
    if not text or text == "—":
        return None
    try:
        return float(text.replace("%", "").replace("+", ""))
    except ValueError:
        return None


def _draw_footer(fig: plt.Figure, page_num: int, total_pages: int) -> None:
    fig.text(
        0.5,
        0.02,
        f"Page {page_num} of {total_pages}",
        ha="center",
        va="bottom",
        fontsize=8,
        color=_MUTED,
    )


def _draw_cover(fig: plt.Figure, result: FundLedgerResult) -> None:
    f = result.fund
    cur = f.currency
    sym = _currency_prefix(cur)

    fig.patch.set_facecolor(_WHITE)
    fig.text(0.08, 0.88, "TRADE & FUND REPORT", fontsize=11, color=_MUTED, weight="bold")
    fig.text(
        0.08,
        0.80,
        f.name,
        fontsize=26,
        weight="bold",
        color=_NAVY,
    )
    fig.text(0.08, 0.74, f"{f.owner}  ·  {cur}", fontsize=12, color=_SLATE)
    fig.text(
        0.08,
        0.68,
        result.as_of.strftime("%A %d %B %Y"),
        fontsize=11,
        color=_MUTED,
    )

    metrics = [
        ("Net Asset Value", _fmt_money(result.nav, cur)),
        ("Cash Balance", _fmt_money(result.cash_balance, cur)),
        ("Positions (Marked)", _fmt_money(result.positions_value, cur)),
        ("Invested Capital", _fmt_money(result.invested_capital, cur)),
        ("Realized P/L", _fmt_money(result.cumulative_realized_pl, cur)),
        ("Fund ROI", _fmt_pct(result.fund_roi_pct)),
    ]
    y = 0.52
    for label, value in metrics:
        box = FancyBboxPatch(
            (0.08, y - 0.055),
            0.84,
            0.075,
            boxstyle="round,pad=0.008,rounding_size=0.02",
            linewidth=0.6,
            edgecolor="#dde3ea",
            facecolor=_STRIPE,
            transform=fig.transFigure,
        )
        fig.add_artist(box)
        fig.text(0.11, y - 0.018, label, fontsize=10, color=_SLATE)
        val_color = _NAVY
        if label == "Fund ROI":
            roi = result.fund_roi_pct
            if roi is not None:
                val_color = _POS if roi > 0 else _NEG if roi < 0 else _SLATE
        elif label == "Realized P/L":
            val_color = _POS if result.cumulative_realized_pl > 0 else _NEG if result.cumulative_realized_pl < 0 else _SLATE
        fig.text(0.91, y - 0.018, value, ha="right", fontsize=11, weight="bold", color=val_color)
        y -= 0.09

    fig.text(
        0.08,
        0.08,
        "Positions and P/L from trade ledger · marks from latest quotes",
        fontsize=8,
        color=_MUTED,
    )


def _style_table(
    table,
    *,
    pl_cols: set[int] | None = None,
    pct_cols: set[int] | None = None,
) -> None:
    pl_cols = pl_cols or set()
    pct_cols = pct_cols or set()
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#dde3ea")
        cell.set_linewidth(0.4)
        if row == 0:
            cell.set_facecolor(_NAVY)
            cell.set_text_props(color=_WHITE, weight="bold", fontsize=8)
            cell.set_height(0.06)
            continue
        cell.set_facecolor(_STRIPE if row % 2 == 0 else _WHITE)
        cell.set_text_props(fontsize=7.5)
        text = cell.get_text().get_text()
        if col in pl_cols or col in pct_cols:
            val = _parse_pct_cell(text) if col in pct_cols else _parse_money_cell(text)
            if val is not None and val > 0:
                cell.set_text_props(color=_POS)
            elif val is not None and val < 0:
                cell.set_text_props(color=_NEG)


def _draw_table_page(
    fig: plt.Figure,
    *,
    section_title: str,
    subtitle: str,
    headers: list[str],
    rows: list[list[str]],
    col_widths: list[float] | None = None,
    left_cols: set[int] | None = None,
    pl_cols: set[int] | None = None,
    pct_cols: set[int] | None = None,
) -> None:
    fig.patch.set_facecolor(_WHITE)
    fig.text(0.06, 0.94, section_title, fontsize=14, weight="bold", color=_NAVY)
    if subtitle:
        fig.text(0.06, 0.90, subtitle, fontsize=9, color=_MUTED)

    ax = fig.add_axes([0.04, 0.06, 0.92, 0.82])
    ax.axis("off")
    if not rows:
        ax.text(0.5, 0.5, "No data.", ha="center", va="center", color=_MUTED)
        return

    left_cols = left_cols or {0}
    cell_text = [headers] + rows
    table = ax.table(
        cellText=cell_text,
        loc="upper center",
        cellLoc="right",
        colWidths=col_widths,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)
    table.scale(1.0, 1.35)

    for (row, col), cell in table.get_celld().items():
        if col in left_cols:
            cell.set_text_props(ha="left")
        if row > 0 and col == 0:
            cell.set_text_props(ha="left", weight="bold")

    _style_table(table, pl_cols=pl_cols, pct_cols=pct_cols)


def _trade_rows(result: FundLedgerResult) -> list[list[str]]:
    cur = result.fund.currency
    rows: list[list[str]] = []
    for pt in result.processed:
        tr = pt.trade
        rows.append(
            [
                tr.trade_date.strftime("%Y-%m-%d"),
                tr.ticker,
                tr.side.upper(),
                _fmt_shares(tr.shares),
                _fmt_money(tr.price, cur),
                _fmt_money(pt.cash_impact, cur),
                _fmt_money(pt.cash_after, cur),
                _fmt_money(pt.realized_pl, cur) if pt.realized_pl is not None else "—",
                _fmt_pct(pt.trade_roi_pct),
            ]
        )
    return rows


def _position_rows(result: FundLedgerResult) -> list[list[str]]:
    cur = result.fund.currency
    rows: list[list[str]] = []
    for p in sorted(result.positions, key=lambda x: -(x.pct_of_nav or 0)):
        rows.append(
            [
                p.ticker,
                _fmt_shares(p.shares),
                _fmt_money(p.avg_cost, cur),
                _fmt_money(p.mark_price, cur) if p.mark_price is not None else "—",
                _fmt_money(p.market_value, cur) if p.market_value is not None else "—",
                _fmt_money(p.unrealized_pl, cur) if p.unrealized_pl is not None else "—",
                _fmt_pct(p.unrealized_roi_pct),
                _fmt_pct(p.pct_of_nav),
                _fmt_money(p.cumulative_realized_pl, cur),
            ]
        )
    return rows


def _stock_rows(result: FundLedgerResult) -> list[list[str]]:
    cur = result.fund.currency
    rows: list[list[str]] = []
    for s in sorted(result.stock_summaries, key=lambda x: x.ticker):
        rows.append(
            [
                s.ticker,
                _fmt_shares(s.shares) if s.shares > 0 else "—",
                _fmt_money(s.avg_cost, cur) if s.shares > 0 else "—",
                _fmt_money(s.avg_profit_per_share, cur) if s.shares > 0 else "—",
                _fmt_money(s.cumulative_realized_pl, cur),
                _fmt_money(s.unrealized_pl, cur) if s.unrealized_pl is not None else "—",
                _fmt_money(s.total_pl, cur) if s.total_pl is not None else "—",
            ]
        )
    return rows


def _history_rows(result: FundLedgerResult, performance_history: list[dict]) -> list[list[str]]:
    cur = result.fund.currency
    return [
        [
            str(h["snapshot_date"]),
            _fmt_money(float(h["cash_balance"]), cur),
            _fmt_money(float(h["positions_value"]), cur),
            _fmt_money(float(h["nav"]), cur),
            _fmt_pct(float(h["roi_pct"])) if h["roi_pct"] is not None else "—",
        ]
        for h in performance_history
    ]


def _paginate(rows: list[list[str]], page_size: int) -> list[list[list[str]]]:
    if not rows:
        return [[]]
    return [rows[i : i + page_size] for i in range(0, len(rows), page_size)]


def render_trade_report_pdf(
    result: FundLedgerResult,
    performance_history: list[dict] | None,
    output_path: Path,
) -> Path:
    """Write a multi-page professional PDF trade & fund report."""
    _apply_style()
    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    trade_pages = _paginate(_trade_rows(result), 26)
    history = performance_history or []
    hist_pages = _paginate(_history_rows(result, history), 28) if history else []

    # Cover + positions + stock summary + trade pages + history pages
    total_pages = 1 + 1 + 1 + len(trade_pages) + len(hist_pages)

    with PdfPages(output_path) as pdf:
        page = 1

        fig = plt.figure(figsize=(8.5, 11))
        _draw_cover(fig, result)
        _draw_footer(fig, page, total_pages)
        pdf.savefig(fig, facecolor=fig.get_facecolor())
        plt.close(fig)
        page += 1

        fig = plt.figure(figsize=(8.5, 11))
        _draw_table_page(
            fig,
            section_title="Open Positions",
            subtitle="Marked at report date · sorted by weight in NAV",
            headers=[
                "Ticker",
                "Shares",
                "Avg Cost",
                "Mark",
                "Mkt Value",
                "Unrealized P/L",
                "Unrealized ROI",
                "% of NAV",
                "Realized P/L",
            ],
            rows=_position_rows(result),
            col_widths=[0.09, 0.08, 0.1, 0.09, 0.11, 0.11, 0.09, 0.08, 0.11],
            left_cols={0},
            pl_cols={5, 8},
            pct_cols={6, 7},
        )
        _draw_footer(fig, page, total_pages)
        pdf.savefig(fig, facecolor=fig.get_facecolor())
        plt.close(fig)
        page += 1

        fig = plt.figure(figsize=(8.5, 11))
        _draw_table_page(
            fig,
            section_title="Stock Performance Summary",
            subtitle="Average cost and profit per share across all tickers traded",
            headers=[
                "Ticker",
                "Shares",
                "Avg Cost",
                "Avg Profit/Share",
                "Realized P/L",
                "Unrealized P/L",
                "Total P/L",
            ],
            rows=_stock_rows(result),
            col_widths=[0.1, 0.09, 0.12, 0.12, 0.13, 0.13, 0.13],
            left_cols={0},
            pl_cols={4, 5, 6},
        )
        _draw_footer(fig, page, total_pages)
        pdf.savefig(fig, facecolor=fig.get_facecolor())
        plt.close(fig)
        page += 1

        trade_headers = [
            "Date",
            "Ticker",
            "Side",
            "Shares",
            "Price",
            "Cash Impact",
            "Cash After",
            "Realized P/L",
            "Trade ROI",
        ]
        for chunk_idx, chunk in enumerate(trade_pages):
            fig = plt.figure(figsize=(11, 8.5))
            subtitle = f"Chronological ledger · page {chunk_idx + 1} of {len(trade_pages)}"
            if not chunk:
                subtitle = "No trades recorded"
            _draw_table_page(
                fig,
                section_title="Trade Ledger",
                subtitle=subtitle,
                headers=trade_headers,
                rows=chunk,
                col_widths=[0.1, 0.08, 0.06, 0.08, 0.1, 0.11, 0.12, 0.11, 0.09],
                left_cols={0, 1, 2},
                pl_cols={5, 7},
                pct_cols={8},
            )
            _draw_footer(fig, page, total_pages)
            pdf.savefig(fig, facecolor=fig.get_facecolor())
            plt.close(fig)
            page += 1

        for chunk_idx, chunk in enumerate(hist_pages):
            fig = plt.figure(figsize=(8.5, 11))
            _draw_table_page(
                fig,
                section_title="Fund Performance History",
                subtitle=f"Stored snapshots · page {chunk_idx + 1} of {len(hist_pages)}",
                headers=["Date", "Cash", "Positions", "NAV", "ROI %"],
                rows=chunk,
                col_widths=[0.14, 0.18, 0.18, 0.18, 0.12],
                left_cols={0},
                pct_cols={4},
            )
            _draw_footer(fig, page, total_pages)
            pdf.savefig(fig, facecolor=fig.get_facecolor())
            plt.close(fig)
            page += 1

        d = pdf.infodict()
        d["Title"] = f"Trade report — {result.fund.name}"
        d["Author"] = "Portfolio Progress Mailer"
        d["Subject"] = f"{result.fund.owner} {result.fund.name} as of {result.as_of.isoformat()}"

    return output_path.resolve()
