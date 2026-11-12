from __future__ import annotations

from collections.abc import Sequence

import matplotlib.cm
import matplotlib.patches as mpatches
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.figure import Figure

from reports.heatmap_data import HeatmapColorBy, HeatmapTile, tile_pl_for_color

try:
    import squarify
except ImportError:  # pragma: no cover
    squarify = None  # type: ignore[assignment]

# Surface colors (not the tile P/L colormap above); overridden per the active
# GUI theme via the `palette` argument, this is only the fallback when unset.
_DEFAULT_PALETTE = {
    "bg": "#0d1117",
    "panel": "#161b22",
    "border": "#30363d",
    "fg": "#e6edf3",
    "muted": "#8b949e",
    "accent": "#4a7fd4",
}


def _pl_cmap():
    return LinearSegmentedColormap.from_list(
        "pl_heat",
        ["#b71c1c", "#ef5350", "#ffebee", "#e8f5e9", "#66bb6a", "#1b5e20"],
    )


# Fixed P/L % scale for tile colour: -5% (red) … +5% (green)
_PL_PCT_VMIN = -5.0
_PL_PCT_VMAX = 5.0


def _pl_norm() -> Normalize:
    return Normalize(vmin=_PL_PCT_VMIN, vmax=_PL_PCT_VMAX)


def _text_color(face) -> str:
    r, g, b, _ = face
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return "white" if lum < 0.45 else "#111111"


def _display_ticker(tile: HeatmapTile) -> str:
    """USD (US) listings use the same label style as AUD; suffix * marks US."""
    if tile.currency.upper() == "USD":
        return f"{tile.ticker}*"
    return tile.ticker


def _tile_area(rw: float, rh: float) -> float:
    return rw * rh


def _label_and_font(
    tile: HeatmapTile, rw: float, rh: float, *, color_by: HeatmapColorBy = "overall"
) -> tuple[str, float] | None:
    """One centered label + font size that fits inside the tile."""
    area = _tile_area(rw, rh)
    if area < 0.0018 or rw < 0.032 or rh < 0.038:
        return None

    pct = f"{tile_pl_for_color(tile, color_by):+.1f}%"
    side = min(rw, rh)

    tick = _display_ticker(tile)
    if side < 0.055 or area < 0.0035:
        return tick, min(8.0, max(4.5, side * 95.0))

    if side < 0.085 or area < 0.006:
        return f"{tick}\n{pct}", min(7.5, max(4.0, side * 72.0))

    return f"{tick}\n{pct}", min(13.0, max(5.5, side * 88.0))


def _draw_tile_labels(
    ax,
    patch: mpatches.Rectangle,
    rx: float,
    ry: float,
    rw: float,
    rh: float,
    tile: HeatmapTile,
    tc: str,
    *,
    color_by: HeatmapColorBy = "overall",
) -> None:
    spec = _label_and_font(tile, rw, rh, color_by=color_by)
    if spec is None:
        return

    label, fs = spec
    txt = ax.text(
        rx + rw / 2,
        ry + rh / 2,
        label,
        ha="center",
        va="center",
        fontsize=fs,
        fontweight="bold",
        color=tc,
        linespacing=0.9,
        clip_on=True,
    )
    txt.set_clip_path(patch)


def _layout_rects(
    sizes: Sequence[float],
    x: float,
    y: float,
    dx: float,
    dy: float,
) -> list[tuple[float, float, float, float]]:
    if squarify is None:
        raise RuntimeError("squarify package is required for the heatmap tab (pip install squarify).")
    if not sizes or sum(sizes) <= 0:
        return []
    normed = squarify.normalize_sizes(sizes, dx, dy)
    rects = squarify.squarify(normed, x, y, dx, dy)
    return [(r["x"], r["y"], r["dx"], r["dy"]) for r in rects]


def _draw_tiles_in_rect(
    ax,
    tiles: Sequence[HeatmapTile],
    x: float,
    y: float,
    dx: float,
    dy: float,
    norm: Normalize,
    cmap,
    *,
    color_by: HeatmapColorBy = "overall",
) -> None:
    if not tiles:
        return
    sizes = [t.value for t in tiles]
    rects = _layout_rects(sizes, x, y, dx, dy)
    for tile, (rx, ry, rw, rh) in zip(tiles, rects):
        color = cmap(norm(tile_pl_for_color(tile, color_by)))
        patch = mpatches.Rectangle(
            (rx, ry),
            rw,
            rh,
            facecolor=color,
            edgecolor="#1a1a1a",
            linewidth=0.6,
        )
        ax.add_patch(patch)
        tc = _text_color(color)
        _draw_tile_labels(ax, patch, rx, ry, rw, rh, tile, tc, color_by=color_by)


def build_heatmap_figure(
    tiles: Sequence[HeatmapTile],
    *,
    title: str,
    color_by: HeatmapColorBy = "overall",
    group_by_fund: bool = False,  # ignored; kept for callers
    palette: dict[str, str] | None = None,
) -> Figure:
    pal = {**_DEFAULT_PALETTE, **(palette or {})}
    fig = Figure(figsize=(10, 6.5), dpi=100, facecolor=pal["bg"])
    ax = fig.add_axes([0.02, 0.08, 0.76, 0.86])
    ax.set_facecolor(pal["bg"])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.invert_yaxis()
    ax.axis("off")

    if not tiles:
        ax.text(
            0.5,
            0.5,
            "No positions with value for this filter.\n"
            "Try another owner/fund or add mark prices via --snapshot-stocks-daily.",
            ha="center",
            va="center",
            fontsize=12,
            color=pal["fg"],
            wrap=True,
        )
        fig.suptitle(title, color=pal["fg"], fontsize=13, y=0.98)
        return fig

    norm = _pl_norm()
    cmap = _pl_cmap()

    # Single treemap: ticker + % only (fund filter is in the title, not on tiles).
    _draw_tiles_in_rect(ax, list(tiles), 0, 0, 1, 1, norm, cmap, color_by=color_by)

    sm = matplotlib.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cax = fig.add_axes([0.80, 0.25, 0.025, 0.5])
    cb = fig.colorbar(sm, cax=cax)
    pl_label = "Day P/L %" if color_by == "day" else "Unrealized P/L %"
    cb.set_label(pl_label, color=pal["fg"], fontsize=9)
    cb.ax.yaxis.set_tick_params(color=pal["fg"], labelcolor=pal["fg"])
    cb.outline.set_edgecolor(pal["border"])

    fig.suptitle(title, color=pal["fg"], fontsize=13, fontweight="bold", y=0.98)
    return fig
