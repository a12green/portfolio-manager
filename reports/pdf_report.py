from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
import sqlite3
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch

from reports.ledger_pdf_data import collect_all_owner_series

_OWNER_ORDER = (OWNER_A, OWNER_C, OWNER_B)
_DISPLAY = {OWNER_A: OWNER_A, OWNER_C: OWNER_C, OWNER_B: OWNER_B}
_PALETTE = {
    OWNER_A: "#1a7f4b",
    OWNER_C: "#2c5f9e",
    OWNER_B: "#9b4d96",
}
_ACCENT = {
    OWNER_A: "#8fd4a8",
    OWNER_C: "#8eb8e8",
    OWNER_B: "#d9a8d4",
}
_POS = "#2e7d32"
_NEG = "#c62828"
_NEUTRAL = "#5f6368"


def _apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "#fafafa",
            "axes.facecolor": "#ffffff",
            "axes.edgecolor": "#cccccc",
            "axes.labelcolor": "#333333",
            "axes.titleweight": "bold",
            "axes.titlesize": 11,
            "font.size": 9,
            "grid.alpha": 0.35,
            "grid.linestyle": "--",
        }
    )


def _fmt_money(v: float | None, compact: bool = False) -> str:
    if v is None:
        return "—"
    if compact and abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if compact and abs(v) >= 1_000:
        return f"${v / 1_000:.1f}k"
    return f"${v:,.0f}"


def _fmt_delta(cur: float | None, prev: float | None) -> tuple[str, str]:
    if cur is None or prev is None:
        return "—", _NEUTRAL
    d = cur - prev
    sign = "+" if d > 0 else ""
    color = _POS if d > 0 else _NEG if d < 0 else _NEUTRAL
    return f"{sign}{d:,.0f}", color


def _style_ax(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y")


def _placeholder(ax: plt.Axes, message: str) -> None:
    ax.axis("off")
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=10, color=_NEUTRAL, wrap=True)


def _draw_kpi_strip(
    fig: plt.Figure,
    gs_slot,
    metrics: list[tuple[str, str, str, str]],
    accent: str,
) -> None:
    """metrics: (title, value, delta_text, delta_color)"""
    inner = gs_slot.subgridspec(1, len(metrics), wspace=0.12)
    for i, (title, value, delta, dcolor) in enumerate(metrics):
        ax = fig.add_subplot(inner[i])
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        box = FancyBboxPatch(
            (0.02, 0.08),
            0.96,
            0.84,
            boxstyle="round,pad=0.02,rounding_size=0.08",
            linewidth=0,
            facecolor=accent,
            alpha=0.35,
            transform=ax.transAxes,
        )
        ax.add_patch(box)
        ax.text(0.5, 0.72, title, ha="center", va="center", fontsize=8, color=_NEUTRAL)
        ax.text(0.5, 0.42, value, ha="center", va="center", fontsize=13, weight="bold")
        ax.text(0.5, 0.18, delta, ha="center", va="center", fontsize=8, color=dcolor)


def _draw_stock_trend(ax: plt.Axes, pts: list[tuple[date, float]], color: str) -> None:
    if len(pts) < 2:
        _placeholder(ax, "Need 2+ daily snapshots for trend.")
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ax.fill_between(xs, ys, alpha=0.25, color=color)
    ax.plot(xs, ys, color=color, linewidth=2.2, marker="o", markersize=4)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")
    ax.set_ylabel("Market value (USD)")
    ax.set_title("Stock Portfolio Value")
    _style_ax(ax)


def _draw_holdings_pie(ax: plt.Axes, holdings: list[dict], color: str) -> None:
    if not holdings:
        _placeholder(ax, "No holdings snapshot.")
        return
    labels = [str(h["ticker"]) for h in holdings]
    sizes = [float(h["mkt_value"]) for h in holdings]
    if sum(sizes) <= 0:
        _placeholder(ax, "No market value data.")
        return
    # Top 8 + Other
    if len(sizes) > 8:
        top = 8
        other = sum(sizes[top:])
        labels = labels[:top] + ["Other"]
        sizes = sizes[:top] + [other]
    cmap = plt.cm.Greens if color == _PALETTE[OWNER_A] else plt.cm.Blues
    if color == _PALETTE[OWNER_B]:
        cmap = plt.cm.Purples
    colors = cmap(np.linspace(0.35, 0.85, len(sizes)))
    wedges, _, autotexts = ax.pie(
        sizes,
        labels=labels,
        autopct=lambda p: f"{p:.0f}%" if p >= 4 else "",
        startangle=90,
        colors=colors,
        textprops={"fontsize": 7},
        pctdistance=0.75,
    )
    for t in autotexts:
        t.set_fontsize(7)
    ax.set_title("Allocation by Holding")


def _draw_pl_bars(ax: plt.Axes, holdings: list[dict]) -> None:
    if not holdings:
        _placeholder(ax, "No P/L data.")
        return
    tickers = [str(h["ticker"]) for h in holdings[:12]]
    pcts = [float(h["pl_pct"]) for h in holdings[:12]]
    colors = [_POS if p >= 0 else _NEG for p in pcts]
    y = np.arange(len(tickers))
    ax.barh(y, pcts, color=colors, alpha=0.85, height=0.65)
    ax.set_yticks(y)
    ax.set_yticklabels(tickers, fontsize=7)
    ax.axvline(0, color=_NEUTRAL, linewidth=0.8)
    ax.set_xlabel("P/L %")
    ax.set_title("Return by Holding (Latest)")
    ax.invert_yaxis()
    _style_ax(ax)


def _draw_fund_total_bars(ax: plt.Axes, lines: list[dict], color: str) -> None:
    usable = [r for r in lines if r.get("fund_total_usd") is not None]
    if not usable:
        _placeholder(ax, "No trade fund NAV data.")
        return
    names = [str(r["fund_name"])[:18] for r in usable]
    vals = [float(r["fund_total_usd"]) for r in usable]
    y = np.arange(len(names))
    ax.barh(y, vals, color=color, alpha=0.85, height=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xlabel("USD")
    ax.set_title("Fund Total")
    ax.invert_yaxis()
    _style_ax(ax)


def _draw_fund_profit_bars(ax: plt.Axes, lines: list[dict]) -> None:
    usable = [r for r in lines if r.get("total_profit_usd") is not None]
    if not usable:
        _placeholder(ax, "No trade fund P/L data.")
        return
    names = [str(r["fund_name"])[:18] for r in usable]
    vals = [float(r["total_profit_usd"]) for r in usable]
    colors = [_POS if v >= 0 else _NEG for v in vals]
    y = np.arange(len(names))
    ax.barh(y, vals, color=colors, alpha=0.85, height=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=7)
    ax.axvline(0, color=_NEUTRAL, linewidth=0.8)
    ax.set_xlabel("USD")
    ax.set_title("Profit")
    ax.invert_yaxis()
    _style_ax(ax)


def _draw_fund_cash_grouped(ax: plt.Axes, lines: list[dict], color: str) -> None:
    """Grouped bars: cash invested, balance, active purchases per fund."""
    usable = [
        r
        for r in lines
        if any(r.get(k) is not None for k in ("cash_invested", "current_cash_balance", "active_purchase_amount"))
    ]
    if not usable:
        _placeholder(
            ax,
            "No trade funds for this owner.\n"
            "Add funds and trades in the portfolio manager.",
        )
        return
    names = [str(r["fund_name"])[:14] for r in usable]
    x = np.arange(len(names))
    w = 0.25
    b = [float(r["cash_invested"] or 0) for r in usable]
    d = [float(r["current_cash_balance"] or 0) for r in usable]
    e = [float(r["active_purchase_amount"] or 0) for r in usable]
    ax.bar(x - w, b, w, label="Invested Capital", color=color, alpha=0.5)
    ax.bar(x, d, w, label="Cash Balance", color=color, alpha=0.75)
    ax.bar(x + w, e, w, label="Positions", color=color, alpha=1.0)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=35, ha="right", fontsize=7)
    ax.set_ylabel("USD")
    ax.set_title("Cash Metrics by Fund")
    ax.legend(loc="upper right", fontsize=7, framealpha=0.9)
    _style_ax(ax)


def _draw_fund_trends(ax: plt.Axes, fund_pts: list[tuple[date, float | None, float | None]], color: str) -> None:
    if len(fund_pts) < 2:
        _placeholder(ax, "Need 2+ fund snapshots.\nRun: python main.py --trade-snapshot --trade-fund <name>")
        return
    xs = [p[0] for p in fund_pts]
    i_vals = [p[1] if p[1] is not None else np.nan for p in fund_pts]
    k_vals = [p[2] if p[2] is not None else np.nan for p in fund_pts]
    ax.plot(xs, i_vals, color=color, linewidth=2, marker="o", label="Total NAV")
    ax2 = ax.twinx()
    ax2.plot(xs, k_vals, color=_NEUTRAL, linewidth=1.8, marker="s", linestyle="--", label="Total P/L")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")
    ax.set_ylabel("Total NAV", color=color)
    ax2.set_ylabel("Total P/L", color=_NEUTRAL)
    ax.set_title("Weekly Fund Trends (Summed)")
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=7)
    _style_ax(ax)
    ax2.spines["top"].set_visible(False)


def _draw_dashboard(
    fig: plt.Figure,
    generated_on: date,
    latest_by_owner: dict[str, tuple[float | None, float | None, float | None]],
) -> None:
    """Compare owners: latest stock value vs fund total."""
    fig.suptitle("Portfolio Dashboard", fontsize=16, weight="bold", y=0.98)
    fig.text(0.5, 0.93, f"As of {generated_on.isoformat()}", ha="center", fontsize=10, color=_NEUTRAL)

    owners = [o for o in _OWNER_ORDER if o in latest_by_owner]
    labels = [_DISPLAY.get(o, o) for o in owners]
    stock_vals = [latest_by_owner[o][0] or 0 for o in owners]
    fund_vals = [latest_by_owner[o][1] or 0 for o in owners]
    colors = [_PALETTE[o] for o in owners]

    ax1 = fig.add_subplot(2, 1, 1)
    x = np.arange(len(labels))
    bars = ax1.bar(x, stock_vals, color=colors, alpha=0.9, width=0.55)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels)
    ax1.set_ylabel("USD")
    ax1.set_title("Latest Stock Market Value")
    _style_ax(ax1)
    for bar, val in zip(bars, stock_vals):
        if val:
            ax1.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                _fmt_money(val, compact=True),
                ha="center",
                va="bottom",
                fontsize=8,
            )

    ax2 = fig.add_subplot(2, 1, 2)
    bars2 = ax2.bar(x, fund_vals, color=colors, alpha=0.9, width=0.55)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels)
    ax2.set_ylabel("USD")
    ax2.set_title("Latest Fund Total")
    _style_ax(ax2)
    for bar, val in zip(bars2, fund_vals):
        if val:
            ax2.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                _fmt_money(val, compact=True),
                ha="center",
                va="bottom",
                fontsize=8,
            )

    fig.subplots_adjust(top=0.88, hspace=0.35)


def render_pdf(conn: sqlite3.Connection, output_path: Path, generated_on: date | None = None) -> Path:
    _apply_style()
    generated_on = generated_on or date.today()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    owner_series = collect_all_owner_series(conn)
    owners = [o for o in _OWNER_ORDER if o in owner_series] or sorted(owner_series)

    latest_dashboard: dict[str, tuple[float | None, float | None, float | None]] = {}
    for owner in owners:
        data = owner_series[owner]
        latest_dashboard[owner] = (
            data["latest_positions"],
            data["latest_nav"],
            data["latest_profit"],
        )

    with PdfPages(output_path) as pdf:
        # Cover
        fig = plt.figure(figsize=(8.5, 11))
        fig.patch.set_facecolor("#f0f4f8")
        fig.text(0.5, 0.62, "Portfolio History Report", ha="center", fontsize=22, weight="bold", color="#1a1a1a")
        fig.text(
            0.5,
            0.52,
            f"Generated {generated_on.strftime('%A %d %B %Y')}",
            ha="center",
            fontsize=12,
            color=_NEUTRAL,
        )
        fig.text(
            0.5,
            0.42,
            "Trade ledger · Fund NAV history",
            ha="center",
            fontsize=11,
            color=_NEUTRAL,
        )
        for i, owner in enumerate(owners):
            fig.text(
                0.5,
                0.30 - i * 0.06,
                f"● {_DISPLAY.get(owner, owner)}",
                ha="center",
                fontsize=11,
                color=_PALETTE.get(owner, _NEUTRAL),
            )
        pdf.savefig(fig)
        plt.close(fig)

        # Dashboard
        fig_d = plt.figure(figsize=(8.5, 11))
        _draw_dashboard(fig_d, generated_on, latest_dashboard)
        pdf.savefig(fig_d)
        plt.close(fig_d)

        for owner in owners:
            label = _DISPLAY.get(owner, owner)
            color = _PALETTE.get(owner, _NEUTRAL)
            accent = _ACCENT.get(owner, "#e8eef5")
            data = owner_series[owner]

            stock_pts = data["positions_pts"]
            fund_pts = data["fund_pts"]
            holdings = data["holdings"]
            latest_lines = data["fund_lines"]

            d_stock, c_stock = _fmt_delta(data["latest_positions"], data["prev_positions"])
            d_i, c_i = _fmt_delta(data["latest_nav"], data["prev_nav"])
            d_k, c_k = _fmt_delta(data["latest_profit"], data["prev_profit"])

            # --- Stocks page (graphical) ---
            fig_s = plt.figure(figsize=(8.5, 11))
            fig_s.suptitle(f"{label} — Stocks", fontsize=15, weight="bold", color=color, y=0.98)
            gs = fig_s.add_gridspec(
                4,
                2,
                height_ratios=[0.14, 0.38, 0.24, 0.24],
                hspace=0.45,
                wspace=0.28,
            )

            kpi_metrics = [
                ("Positions Value", _fmt_money(data["latest_positions"]), f"Δ {d_stock}", c_stock),
                ("Total NAV", _fmt_money(data["latest_nav"]), f"Δ {d_i}", c_i),
                ("Total P/L", _fmt_money(data["latest_profit"]), f"Δ {d_k}", c_k),
            ]
            _draw_kpi_strip(fig_s, gs[0, :], kpi_metrics, accent)

            ax_trend = fig_s.add_subplot(gs[1, :])
            _draw_stock_trend(ax_trend, stock_pts, color)

            ax_pie = fig_s.add_subplot(gs[2, 0])
            _draw_holdings_pie(ax_pie, holdings, color)

            ax_pl = fig_s.add_subplot(gs[2, 1])
            _draw_pl_bars(ax_pl, holdings)

            # Stock value mini sparkline duplicate removed — use bottom for combined stocks+funds context
            ax_note = fig_s.add_subplot(gs[3, :])
            ax_note.axis("off")
            total_cost = sum(float(h.get("cost_basis") or 0) for h in holdings)
            total_pl = sum(float(h.get("pl_dollar") or 0) for h in holdings)
            pl_pct = (total_pl / total_cost * 100) if total_cost else 0
            ax_note.text(
                0.5,
                0.5,
                f"{len(holdings)} holdings · Combined P/L {_fmt_money(total_pl)} ({pl_pct:+.1f}%)",
                ha="center",
                va="center",
                fontsize=10,
                color=_NEUTRAL,
                transform=ax_note.transAxes,
            )

            pdf.savefig(fig_s)
            plt.close(fig_s)

            # --- Funds page (graphical) ---
            fig_f = plt.figure(figsize=(8.5, 11))
            fig_f.suptitle(f"{label} — Funds", fontsize=15, weight="bold", color=color, y=0.98)
            gf = fig_f.add_gridspec(3, 2, height_ratios=[0.32, 0.32, 0.36], hspace=0.42, wspace=0.3)

            ax_ft = fig_f.add_subplot(gf[0, 0])
            _draw_fund_total_bars(ax_ft, latest_lines, color)

            ax_fp = fig_f.add_subplot(gf[0, 1])
            _draw_fund_profit_bars(ax_fp, latest_lines)

            ax_fc = fig_f.add_subplot(gf[1, :])
            _draw_fund_cash_grouped(ax_fc, latest_lines, color)

            ax_ftr = fig_f.add_subplot(gf[2, :])
            _draw_fund_trends(ax_ftr, fund_pts, color)

            pdf.savefig(fig_f)
            plt.close(fig_f)

    return output_path
