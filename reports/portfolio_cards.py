"""Columnar holdings table with intraday sparklines for the portfolio tab."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from datetime import date
from tkinter import ttk

# Match trade_gui dark palette
_CARD_BG = "#161b22"
_HEADER_BG = "#21262d"
_CARD_BORDER = "#30363d"
_FG = "#ffffff"
_GOOD = "#3fb950"
_BAD = "#f85149"
_SPARK_GOOD = "#3fb950"
_SPARK_BAD = "#f85149"
_SPARK_FILL_GOOD = "#238636"
_SPARK_FILL_BAD = "#da3633"
_BASELINE = "#6e7681"
_STRIPE_BG = "#1c2128"

SPARK_W = 72
SPARK_H = 28
# Intraday sparklines use a shared % scale so 1% looks the same on every row.
SPARK_SCALE_PCT = 3.0
_FONT = "Helvetica"
_SIZE = 13

# (field_key, heading, width_px, anchor)
_COLUMNS: tuple[tuple[str, str, int, str], ...] = (
    ("spark", "Day", SPARK_W + 8, "center"),
    ("name", "Name", 140, "w"),
    ("qty", "Qty", 72, "e"),
    ("pct", "% Port", 64, "e"),
    ("prev_close", "Prev Close", 84, "e"),
    ("last", "Mark", 84, "e"),
    ("price_diff", "vs Cost", 84, "e"),
    ("day_chg", "Day $", 80, "e"),
    ("day_pct", "Day %", 68, "e"),
    ("day_hi", "Day High", 84, "e"),
    ("day_lo", "Day Low", 84, "e"),
    ("hi52", "52W High", 84, "e"),
    ("lo52", "52W Low", 84, "e"),
    ("pl", "P/L", 92, "e"),
    ("pl_pct", "P/L %", 68, "e"),
)


@dataclass(frozen=True)
class HoldingCardData:
    ticker: str
    name: str
    loc: str
    shares: float
    avg_cost: float
    last: float
    day_change: float | None
    day_change_pct: float | None
    pct_port: float
    mkt_val: float
    pl_d: float
    pl_p: float
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    w52_high: float | None = None
    w52_low: float | None = None
    sparkline: tuple[float, ...] | None = None
    previous_close: float | None = None
    instrument_type: str = "stock"
    underlying: str | None = None
    strike: float | None = None
    expiry: date | None = None
    put_call: str | None = None
    contract_multiplier: float = 1.0
    underlying_last: float | None = None


def _spark_pct(price: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return (float(price) / baseline - 1.0) * 100.0


def _spark_y(pct: float, height: int, *, scale: float = SPARK_SCALE_PCT) -> float:
    """Map day % change to canvas y; +scale = top, −scale = bottom, 0 = centre."""
    clamped = max(-scale, min(scale, pct))
    t = (scale - clamped) / (2.0 * scale)
    return 1.0 + (height - 2) * t


def draw_sparkline(
    canvas: tk.Canvas,
    prices: tuple[float, ...] | list[float],
    *,
    width: int = SPARK_W,
    height: int = SPARK_H,
    baseline: float | None = None,
    scale_pct: float = SPARK_SCALE_PCT,
) -> None:
    """Intraday sparkline on a fixed % scale (same 1% amplitude on every row)."""
    canvas.delete("all")
    canvas.configure(width=width, height=height)
    if len(prices) < 2:
        canvas.create_line(1, height // 2, width - 1, height // 2, fill=_BASELINE, dash=(2, 2))
        return

    base = float(baseline if baseline is not None else prices[0])
    if base <= 0:
        canvas.create_line(1, height // 2, width - 1, height // 2, fill=_BASELINE, dash=(2, 2))
        return

    pcts = [_spark_pct(p, base) for p in prices]
    base_y = _spark_y(0.0, height, scale=scale_pct)

    def px(i: int, pct: float) -> tuple[float, float]:
        x = 1 + (width - 2) * i / (len(prices) - 1)
        y = _spark_y(pct, height, scale=scale_pct)
        return x, y

    pts = [px(i, pcts[i]) for i in range(len(pcts))]
    canvas.create_line(1, base_y, width - 1, base_y, fill=_BASELINE, dash=(2, 2))

    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        mid_pct = (pcts[i] + pcts[i + 1]) / 2.0
        above = mid_pct >= 0.0
        fill = _SPARK_FILL_GOOD if above else _SPARK_FILL_BAD
        line = _SPARK_GOOD if above else _SPARK_BAD
        poly = [x0, y0, x1, y1, x1, base_y, x0, base_y]
        canvas.create_polygon(poly, fill=fill, outline="")
        canvas.create_line(x0, y0, x1, y1, fill=line, width=1.0, smooth=False)


def _fmt_price(amount: float | None, loc: str = "") -> str:
    if amount is None:
        return "—"
    if str(loc or "").upper() == "AUS":
        return f"A${amount:,.2f}"
    return f"${amount:,.2f}"


def _fmt_change(amount: float | None, loc: str = "") -> str:
    if amount is None:
        return "—"
    sign = "+" if amount > 0 else ""
    if str(loc or "").upper() == "AUS":
        return f"{sign}A${amount:,.2f}"
    return f"{sign}${amount:,.2f}"


def _fmt_change_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.2f}%"


def _pl_color(amount: float | None) -> str:
    if amount is None:
        return _FG
    return _GOOD if amount >= 0 else _BAD


@dataclass
class _RowWidgets:
    cells: list[tk.Label]
    spark_cell: tk.Frame
    spark_canvas: tk.Canvas


class PortfolioCardsView(ttk.Frame):
    """Scrollable columnar holdings table with sparklines and a fixed header row."""

    _HEADER_H = 40

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(master, **kwargs)
        outer = tk.Frame(self, bg=_CARD_BG)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        self._header_canvas = tk.Canvas(
            outer,
            height=self._HEADER_H,
            highlightthickness=0,
            bg=_HEADER_BG,
        )
        self._header_inner = tk.Frame(self._header_canvas, bg=_HEADER_BG)
        self._header_win = self._header_canvas.create_window(
            (0, 0), window=self._header_inner, anchor=tk.NW
        )
        self._header_table = tk.Frame(self._header_inner, bg=_HEADER_BG)
        self._header_table.pack(anchor=tk.NW)
        self._header_canvas.grid(row=0, column=0, sticky="ew", columnspan=2)

        self._canvas = tk.Canvas(outer, highlightthickness=0, bg=_CARD_BG)
        self._vscroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=self._canvas.yview)
        self._hscroll = ttk.Scrollbar(outer, orient=tk.HORIZONTAL, command=self._scroll_x)
        self._inner = tk.Frame(self._canvas, bg=_CARD_BG)
        self._win = self._canvas.create_window((0, 0), window=self._inner, anchor=tk.NW)
        self._canvas.configure(yscrollcommand=self._vscroll.set, xscrollcommand=self._on_body_xscroll)

        self._canvas.grid(row=1, column=0, sticky="nsew")
        self._vscroll.grid(row=1, column=1, sticky="ns")
        self._hscroll.grid(row=2, column=0, sticky="ew")

        self._table = tk.Frame(self._inner, bg=_CARD_BG)
        self._table.pack(anchor=tk.NW)
        self._row_widgets: list[_RowWidgets] = []
        self._header_labels: list[tk.Label] = []
        self._header_sep: tk.Frame | None = None
        self._empty_label: tk.Label | None = None

        self._inner.bind("<Configure>", self._on_inner_configure)
        self._header_inner.bind("<Configure>", self._on_header_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._header_canvas.bind("<Configure>", self._on_header_canvas_configure)
        self._canvas.bind("<Enter>", self._bind_mousewheel)
        self._canvas.bind("<Leave>", self._unbind_mousewheel)

    def _scroll_x(self, *args) -> None:
        self._canvas.xview(*args)
        self._header_canvas.xview(*args)

    def _on_body_xscroll(self, first: str, last: str) -> None:
        self._hscroll.set(first, last)
        self._header_canvas.xview_moveto(float(first))

    def _on_header_inner_configure(self, _event=None) -> None:
        self._header_canvas.configure(scrollregion=self._header_canvas.bbox("all"))

    def _on_header_canvas_configure(self, event) -> None:
        self._header_canvas.itemconfigure(
            self._header_win, width=max(event.width, self._header_table.winfo_reqwidth())
        )

    def _on_inner_configure(self, _event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfigure(self._win, width=max(event.width, self._table.winfo_reqwidth()))

    def _bind_mousewheel(self, _event=None) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event=None) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def _configure_columns(self) -> None:
        for col, (_key, _heading, width, _anchor) in enumerate(_COLUMNS):
            self._header_table.grid_columnconfigure(col, minsize=width, weight=0)
            self._table.grid_columnconfigure(col, minsize=width, weight=0)

    def _cell_label(
        self,
        parent: tk.Misc,
        text: str,
        *,
        row: int,
        col: int,
        anchor: str,
        bg: str,
        fg: str = _FG,
        bold: bool = False,
    ) -> tk.Label:
        weight = "bold" if bold else "normal"
        tk_anchor = {"w": tk.W, "e": tk.E, "center": tk.CENTER}.get(anchor, tk.W)
        sticky = {"w": "w", "e": "e", "center": ""}.get(anchor, "w")
        lbl = tk.Label(
            parent,
            text=text,
            font=(_FONT, _SIZE, weight),
            fg=fg,
            bg=bg,
            anchor=tk_anchor,
            padx=8,
            pady=5,
        )
        lbl.grid(row=row, column=col, sticky=sticky, padx=(0, 1))
        return lbl

    def _ensure_header(self) -> None:
        if self._header_labels:
            self._show_header()
            return
        self._configure_columns()
        for col, (_key, heading, _width, anchor) in enumerate(_COLUMNS):
            self._header_labels.append(
                self._cell_label(
                    self._header_table,
                    heading,
                    row=0,
                    col=col,
                    anchor=anchor,
                    bg=_HEADER_BG,
                    fg=_FG,
                    bold=True,
                )
            )
        self._header_sep = tk.Frame(self._header_table, bg=_CARD_BORDER, height=1)
        self._header_sep.grid(row=1, column=0, columnspan=len(_COLUMNS), sticky="ew")
        self._on_header_inner_configure()

    def _show_header(self) -> None:
        for lbl in self._header_labels:
            lbl.grid()
        if self._header_sep is not None:
            self._header_sep.grid()

    def _hide_header(self) -> None:
        for lbl in self._header_labels:
            lbl.grid_remove()
        if self._header_sep is not None:
            self._header_sep.grid_remove()

    def _ensure_row(self, idx: int) -> _RowWidgets:
        while len(self._row_widgets) <= idx:
            data_row = len(self._row_widgets)
            cells: list[tk.Label] = []
            spark_cell = tk.Frame(self._table, bg=_CARD_BG)
            spark_cell.grid(row=data_row, column=0, sticky="nsew", padx=(4, 1), pady=2)
            spark_canvas = tk.Canvas(
                spark_cell,
                width=SPARK_W,
                height=SPARK_H,
                bg=_CARD_BG,
                highlightthickness=0,
            )
            spark_canvas.pack(anchor=tk.CENTER, pady=4)
            for col, (key, _h, _w, anchor) in enumerate(_COLUMNS):
                if key == "spark":
                    continue
                cells.append(
                    self._cell_label(
                        self._table,
                        "",
                        row=data_row,
                        col=col,
                        anchor=anchor,
                        bg=_CARD_BG,
                    )
                )
            self._row_widgets.append(_RowWidgets(cells=cells, spark_cell=spark_cell, spark_canvas=spark_canvas))
        return self._row_widgets[idx]

    def _hide_extra_rows(self, count: int) -> None:
        for i in range(count, len(self._row_widgets)):
            row = self._row_widgets[i]
            row.spark_cell.grid_remove()
            for lbl in row.cells:
                lbl.grid_remove()

    def clear(self) -> None:
        self._hide_extra_rows(0)
        self._hide_header()
        if self._empty_label is not None:
            self._empty_label.destroy()
            self._empty_label = None

    def set_holdings(self, rows: list[HoldingCardData]) -> None:
        if self._empty_label is not None:
            self._empty_label.destroy()
            self._empty_label = None

        if not rows:
            self._hide_extra_rows(0)
            self._hide_header()
            self._empty_label = tk.Label(
                self._table,
                text="No holdings for this filter.",
                fg=_FG,
                bg=_CARD_BG,
                font=(_FONT, _SIZE),
                pady=16,
                padx=12,
            )
            self._empty_label.grid(row=0, column=0, columnspan=len(_COLUMNS), sticky="w")
            self._on_inner_configure()
            return

        self._ensure_header()
        col_keys = [key for key, _h, _w, _a in _COLUMNS if key != "spark"]

        for idx, d in enumerate(rows):
            bg = _STRIPE_BG if idx % 2 else _CARD_BG

            price_diff = (d.last - d.avg_cost) if d.avg_cost > 0 else None
            values: dict[str, str] = {
                "name": d.name,
                "qty": f"{d.shares:g} ct" if d.instrument_type == "option" else f"{d.shares:g}",
                "pct": f"{d.pct_port:.1f}%",
                "prev_close": _fmt_price(d.previous_close, d.loc),
                "last": _fmt_price(d.last, d.loc),
                "price_diff": _fmt_change(price_diff, d.loc),
                "day_chg": _fmt_change(d.day_change, d.loc),
                "day_pct": _fmt_change_pct(d.day_change_pct),
                "day_hi": _fmt_price(d.day_high, d.loc),
                "day_lo": _fmt_price(d.day_low, d.loc),
                "hi52": _fmt_price(d.w52_high, d.loc),
                "lo52": _fmt_price(d.w52_low, d.loc),
                "pl": _fmt_change(d.pl_d, d.loc),
                "pl_pct": _fmt_change_pct(d.pl_p),
            }
            colors: dict[str, str] = {
                "price_diff": _pl_color(price_diff),
                "prev_close": _pl_color(d.day_change),
                "day_chg": _pl_color(d.day_change),
                "day_pct": _pl_color(d.day_change),
                "pl": _pl_color(d.pl_d),
                "pl_pct": _pl_color(d.pl_p),
            }

            row_w = self._ensure_row(idx)
            row_w.spark_cell.configure(bg=bg)
            row_w.spark_cell.grid()
            row_w.spark_canvas.configure(bg=bg)
            if d.sparkline:
                draw_sparkline(row_w.spark_canvas, d.sparkline, baseline=d.previous_close)
            else:
                draw_sparkline(row_w.spark_canvas, ())

            for lbl, key in zip(row_w.cells, col_keys):
                fg = colors.get(key, _FG)
                weight = "bold" if key == "last" else "normal"
                lbl.configure(
                    text=values[key],
                    bg=bg,
                    fg=fg,
                    font=(_FONT, _SIZE, weight),
                )
                lbl.grid()

        self._hide_extra_rows(len(rows))
        self._on_inner_configure()
