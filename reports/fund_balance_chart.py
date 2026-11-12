from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator
from matplotlib.widgets import RangeSlider

from reports.fund_balance_history import FundBalanceSeries, combine_fund_balance_series


_LINE_COLORS = (
    "#4a7fd4",
    "#3fb950",
    "#f85149",
    "#d2a8ff",
    "#ffa657",
    "#79c0ff",
    "#ff7b72",
    "#a5d6ff",
)

# Surface colors (not the data-series palette above); overridden per the active
# GUI theme via the `palette` argument, this is only the fallback when unset.
_DEFAULT_PALETTE = {
    "bg": "#0d1117",
    "panel": "#161b22",
    "border": "#30363d",
    "fg": "#e6edf3",
    "muted": "#8b949e",
    "accent": "#4a7fd4",
}


def _format_axis_amount(value: float, _pos) -> str:
    sign = "-" if value < 0 else ""
    amount = abs(value)
    if amount >= 1_000_000:
        return f"{sign}${amount / 1_000_000:.1f}M"
    if amount >= 10_000:
        return f"{sign}${amount / 1_000:.0f}K"
    if amount >= 1_000:
        return f"{sign}${amount / 1_000:.1f}K"
    return f"{sign}${amount:,.0f}"


def _style_chart_axes(ax, palette: dict[str, str]) -> None:
    ax.set_axisbelow(True)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=8, min_n_ticks=5))
    ax.yaxis.set_major_formatter(FuncFormatter(_format_axis_amount))
    ax.grid(
        True,
        which="major",
        axis="y",
        color=palette["muted"],
        linestyle="-",
        linewidth=0.8,
        alpha=0.35,
    )
    ax.grid(
        True,
        which="major",
        axis="x",
        color=palette["border"],
        linestyle="--",
        linewidth=0.6,
        alpha=0.45,
    )
    for line in ax.get_ygridlines():
        line.set_zorder(0)
    for line in ax.get_xgridlines():
        line.set_zorder(0)


def _ordinal_to_date(ordinal: float) -> date:
    dt = mdates.num2date(ordinal, tz=None)
    return dt.date()


def _series_in_range(
    series: Sequence[FundBalanceSeries],
    start: date,
    end: date,
) -> list[tuple[FundBalanceSeries, list]]:
    out: list[tuple[FundBalanceSeries, list]] = []
    for s in series:
        pts = [p for p in s.points if start <= p.as_of <= end]
        if len(pts) >= 1:
            out.append((s, pts))
    return out


def build_fund_balance_figure(
    series: Sequence[FundBalanceSeries],
    *,
    title: str,
    value_label: str = "Fund value (NAV)",
    include_total: bool = True,
    palette: dict[str, str] | None = None,
) -> Figure:
    pal = {**_DEFAULT_PALETTE, **(palette or {})}
    fig = Figure(figsize=(10, 6.8), dpi=100, facecolor=pal["bg"])
    ax = fig.add_axes([0.12, 0.22, 0.82, 0.68])
    slider_ax = fig.add_axes([0.12, 0.08, 0.76, 0.04], facecolor=pal["panel"])

    ax.set_facecolor(pal["bg"])
    ax.tick_params(colors=pal["muted"])
    ax.spines["bottom"].set_color(pal["border"])
    ax.spines["left"].set_color(pal["border"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.label.set_color(pal["fg"])
    ax.xaxis.label.set_color(pal["fg"])
    ax.title.set_color(pal["fg"])
    _style_chart_axes(ax, pal)

    if not series:
        ax.text(
            0.5,
            0.5,
            "No fund value history for this filter.\n"
            "Record share trades to build NAV from holdings + cash.",
            ha="center",
            va="center",
            fontsize=12,
            color=pal["fg"],
            transform=ax.transAxes,
        )
        fig.suptitle(title, color=pal["fg"], fontsize=13, y=0.98)
        fig._balance_slider = None  # type: ignore[attr-defined]
        fig._balance_redraw = None  # type: ignore[attr-defined]
        return fig

    display: list[FundBalanceSeries] = list(series)
    if include_total and len(series) > 1:
        owner_label = series[0].owner if len({s.owner for s in series}) == 1 else "All"
        combined = combine_fund_balance_series(series, owner=owner_label)
        if combined is not None:
            display.append(combined)

    all_dates = sorted({p.as_of for s in display for p in s.points})
    min_d = all_dates[0]
    max_d = all_dates[-1]
    if min_d == max_d:
        min_d = min_d - timedelta(days=30)

    window_start = max(min_d, max_d - timedelta(days=365))
    if window_start > min_d and (max_d - window_start).days < 14:
        window_start = min_d

    lines: list = []
    legend = None

    def redraw(start_ord: float, end_ord: float) -> None:
        nonlocal lines, legend
        for ln in lines:
            ln.remove()
        lines = []
        if legend is not None:
            legend.remove()
            legend = None
        start = _ordinal_to_date(start_ord)
        end = _ordinal_to_date(end_ord)
        filtered = _series_in_range(display, start, end)
        if not filtered:
            fig.canvas.draw_idle()
            return
        color_idx = 0
        y_values: list[float] = []
        for s, pts in filtered:
            xs = mdates.date2num([p.as_of for p in pts])
            is_total = s.fund_name == "Total"
            if is_total:
                color = "#f0f6fc"
                nav_lw = 3.0
                inv_lw = 2.0
                zorder = 10
            else:
                color = _LINE_COLORS[color_idx % len(_LINE_COLORS)]
                nav_lw = 2.0
                inv_lw = 1.5
                zorder = 2
                color_idx += 1
            nav_label = f"{s.fund_name} · NAV ({s.currency})"
            inv_label = f"{s.fund_name} · invested ({s.currency})"
            nav_ys = [p.nav for p in pts]
            y_values.extend(nav_ys)
            (ln_nav,) = ax.plot(
                xs,
                nav_ys,
                color=color,
                linewidth=nav_lw,
                marker="o",
                markersize=4 if not is_total else 5,
                label=nav_label,
                zorder=zorder,
            )
            lines.append(ln_nav)
            if any(p.invested > 0 for p in pts):
                inv_ys = [p.invested for p in pts]
                y_values.extend(inv_ys)
                (ln_inv,) = ax.plot(
                    xs,
                    inv_ys,
                    color=color,
                    linewidth=inv_lw,
                    linestyle="--",
                    alpha=0.85 if not is_total else 1.0,
                    label=inv_label,
                    zorder=zorder,
                )
                lines.append(ln_inv)

        pad_days = max(3, (end - start).days // 40)
        ax.set_xlim(
            mdates.date2num(start - timedelta(days=pad_days)),
            mdates.date2num(end + timedelta(days=pad_days)),
        )
        y_lo, y_hi = min(y_values), max(y_values)
        if y_lo == y_hi:
            y_pad = max(abs(y_lo) * 0.05, 1.0)
        else:
            y_pad = max((y_hi - y_lo) * 0.06, 1.0)
        ax.set_ylim(y_lo - y_pad, y_hi + y_pad)

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %Y"))
        _style_chart_axes(ax, pal)
        legend = ax.legend(
            loc="upper left",
            fontsize=8,
            facecolor=pal["panel"],
            edgecolor=pal["border"],
            labelcolor=pal["fg"],
        )
        ax.set_ylabel(value_label)
        ax.set_xlabel("Date")
        fig.canvas.draw_idle()

    min_ord = mdates.date2num(min_d)
    max_ord = mdates.date2num(max_d)
    start_ord = mdates.date2num(window_start)
    slider = RangeSlider(
        slider_ax,
        "Date range",
        min_ord,
        max_ord,
        valinit=(start_ord, max_ord),
        color=pal["accent"],
        track_color=pal["border"],
    )
    slider.label.set_color(pal["fg"])
    slider.valtext.set_color(pal["fg"])

    def on_slide(val) -> None:
        lo, hi = val
        slider.valtext.set_text(
            f"{_ordinal_to_date(lo).strftime('%d %b %Y')} – "
            f"{_ordinal_to_date(hi).strftime('%d %b %Y')}"
        )
        redraw(lo, hi)

    slider.on_changed(on_slide)
    slider.valtext.set_text(
        f"{_ordinal_to_date(start_ord).strftime('%d %b %Y')} – "
        f"{_ordinal_to_date(max_ord).strftime('%d %b %Y')}"
    )
    redraw(start_ord, max_ord)

    fig.suptitle(title, color=pal["fg"], fontsize=13, fontweight="bold", y=0.98)
    fig._balance_slider = slider  # type: ignore[attr-defined]
    fig._balance_redraw = redraw  # type: ignore[attr-defined]
    return fig
