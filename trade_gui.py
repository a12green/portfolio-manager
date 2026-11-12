#!/usr/bin/env python3
"""Desktop GUI for recording trades and viewing current portfolio holdings."""

from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("TkAgg")

import tkinter as tk
from datetime import date, datetime
from tkinter import messagebox, ttk

import ttkbootstrap as ttkb
from dotenv import load_dotenv
from ttkbootstrap.style.theme import Theme

import trades_cli

_OWNERS = (OWNER_A, OWNER_B, OWNER_C)
_PORT_OWNERS = ("All",) + _OWNERS
_SIDES = ("buy", "sell")
_DISPLAY = {OWNER_A: OWNER_A, OWNER_C: OWNER_C, OWNER_B: OWNER_B}
_OTHER_ASSET_CURRENCIES = ("USD", "AUD", "GBP")


def _port_owner_label(owner: str) -> str:
    if owner.strip().lower() == "all":
        return "All owners"
    return _DISPLAY.get(owner, owner)
_PORT_MARKETS = ("All", "Australia", "United States")
_FUND_LOCATIONS = ("All", "United States", "Australia", "United Kingdom")

# Base palette for the default theme (GitHub Dark) and the matplotlib charts'
# fallback when no ttkbootstrap style is active yet. Other theme choices' own
# colors come from ttkbootstrap at runtime (see chart_palette()).
_DARK_BG = "#0d1117"
_DARK_FG = "#e6edf3"
_PL_TAG_FG = "#ffffff"

# GitHub-derived ttkbootstrap theme family (both modes; same accent colors,
# GitHub's own dark and light surface tokens). Registering at import time (no
# Tk root needed yet) queues it for the style engine; see Theme.register().
Theme(
    name="ghdark",
    primary="#1f6feb",
    success="#238636",
    info="#58a6ff",
    warning="#d29922",
    danger="#da3633",
    neutral="#8b949e",
    dark={"background": _DARK_BG, "foreground": _DARK_FG},
    light={"background": "#ffffff", "foreground": "#1f2328"},
).register()

# The embedded matplotlib charts (Fund trends, Heat map) are repainted to match
# whichever of these is active (see chart_palette()), so light backgrounds are
# just as safe to offer as dark ones.
_THEME_CHOICES: tuple[tuple[str, str], ...] = (
    ("GitHub Dark", "ghdark-dark"),
    ("GitHub Light", "ghdark-light"),
    ("One Dark", "one-dark"),
    ("One Light", "one-light"),
    ("Dracula Dark", "dracula-dark"),
    ("Dracula Light", "dracula-light"),
    ("Nord Dark", "nord-dark"),
    ("Nord Light", "nord-light"),
    ("Tokyo Night Dark", "tokyo-night-dark"),
    ("Tokyo Night Light", "tokyo-night-light"),
    ("Gruvbox Dark", "gruvbox-dark"),
    ("Gruvbox Light", "gruvbox-light"),
    ("Solarized Dark", "solarized-dark"),
    ("Solarized Light", "solarized-light"),
    ("Catppuccin Dark", "catppuccin-dark"),
    ("Catppuccin Light", "catppuccin-light"),
)
_THEME_LABEL_BY_NAME = {name: label for label, name in _THEME_CHOICES}
_DEFAULT_THEME_NAME = "ghdark-dark"


def _gui_settings_path() -> Path:
    return Path("data/gui_settings.json")


def _load_gui_settings() -> dict:
    path = _gui_settings_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_gui_settings(settings: dict) -> None:
    path = _gui_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2))
    except OSError:
        pass


def _saved_theme_name() -> str:
    name = _load_gui_settings().get("theme")
    return name if name in _THEME_LABEL_BY_NAME else _DEFAULT_THEME_NAME


def chart_palette() -> dict[str, str]:
    """Surface colors (not the data-series palette) for the embedded matplotlib charts."""
    c = ttkb.Style().colors
    return {
        "bg": c.bg,
        "panel": c.inputbg,
        "border": c.border,
        "fg": c.fg,
        "muted": c.secondary,
        "accent": c.primary,
    }


def _apply_theme(root: tk.Tk, theme_name: str = _DEFAULT_THEME_NAME) -> None:
    """Apply a ttkbootstrap theme (restyles every standard ttk widget); safe to call again to switch live."""
    style = ttkb.Style(theme=theme_name)
    root.configure(bg=style.colors.bg)

    # ttkbootstrap's Treeview build leaves the blank area below the last row
    # unstyled on some platforms (falls back to the system white); force it.
    style.configure(
        "Treeview",
        background=style.colors.inputbg,
        fieldbackground=style.colors.inputbg,
        rowheight=24,
    )
    style.configure(
        "Status.TLabel",
        background=style.colors.inputbg,
        foreground=style.colors.secondary,
        relief="sunken",
        padding=(6, 4),
    )
    style.configure(
        "Muted.TLabel",
        background=style.colors.bg,
        foreground=style.colors.secondary,
    )

    # Spread tabs out across the top instead of a tight cluster at the left.
    style.configure("TNotebook.Tab", padding=(30, 12), font=("TkDefaultFont", 10))

    # Section headers get the accent color instead of the same white as body
    # text, so the window reads with some hierarchy rather than one flat tone.
    style.configure(
        "TLabelframe.Label",
        foreground=style.colors.primary,
        font=("TkDefaultFont", 10, "bold"),
    )

    # Net worth's Total line: a raised card with a big, sign-colored figure.
    style.configure("TotalCard.TFrame", background=style.colors.inputbg, relief="raised", borderwidth=1)
    style.configure(
        "TotalGood.TLabel", background=style.colors.inputbg, foreground=style.colors.success
    )
    style.configure(
        "TotalBad.TLabel", background=style.colors.inputbg, foreground=style.colors.danger
    )


def _setup_tree_stripes(tree: ttk.Treeview, *, avg_colors: bool = False) -> None:
    """(Re-)apply stripe/P&L tag colors from the *currently active* theme; safe to re-call on theme switch.

    ttk.Treeview resolves conflicting tag options by which tag was configured
    *first* (not the order tags are listed on an item, and not last-configured
    as you'd expect) — so semantic tags (P&L color, discrepancy highlight) must
    be configured before "stripe", or a striped odd-numbered row would always
    show the plain stripe color instead of its green/red/amber highlight.
    """
    c = ttkb.Style().colors
    if avg_colors:
        tree.tag_configure("avg_good", background=c.success, foreground=_PL_TAG_FG)
        tree.tag_configure("avg_bad", background=c.danger, foreground=_PL_TAG_FG)
        tree.tag_configure("sheet_disc", background="#3d2c00", foreground="#f0c674")
    tree.tag_configure("stripe", background=c.active)


def _tree_row_tags(row_index: int, extra: str = "") -> tuple[str, ...]:
    tags: list[str] = []
    if row_index % 2 == 1:
        tags.append("stripe")
    if extra:
        tags.append(extra)
    return tuple(tags)


def _pl_row_tag(amount: float | None) -> str:
    """Green background for gain/break-even, red for loss."""
    return "avg_good" if (amount or 0) >= 0 else "avg_bad"


def _parse_trade_date(raw: str) -> date:
    text = raw.strip()
    if not text:
        return date.today()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Use a date like YYYY-MM-DD (or leave blank for today).")


def _fmt_money(amount: float, location: str = "") -> str:
    loc = str(location).upper()
    if loc == "AUS":
        return f"A${amount:,.2f}"
    return f"${amount:,.2f}"


def _fmt_pct(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def _currency_loc(currency: str) -> str:
    c = str(currency or "").upper()
    if c == "AUD":
        return "AUS"
    if c == "GBP":
        return "UK"
    return "US"


def _fmt_fund_money(amount: float | None, currency: str) -> str:
    if amount is None:
        return "—"
    return _fmt_money(amount, _currency_loc(currency))


def _fmt_price_cell(amount: float | None, location: str = "") -> str:
    if amount is None:
        return "—"
    return _fmt_money(amount, location)


def _holding_market(loc: str, ticker: str = "") -> str:
    """AU for Australian listings; otherwise US (sheet Location convention)."""
    loc = str(loc or "").strip().upper()
    tick = str(ticker or "").strip().upper()
    if loc in ("AUS", "AUD", "AU") or tick.startswith("ASX:"):
        return "AU"
    return "US"


def _matches_port_market(loc: str, ticker: str, market_sel: str) -> bool:
    sel = market_sel.strip()
    if not sel or sel == "All":
        return True
    m = _holding_market(loc, ticker)
    if sel.startswith("Austral"):
        return m == "AU"
    if sel.startswith("United") or sel == "US":
        return m == "US"
    return True


class TradeEntryApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self._theme_name = _saved_theme_name()
        _apply_theme(self, self._theme_name)
        self.title("Portfolio manager")
        self.geometry("1000x860")
        self.minsize(800, 560)

        load_dotenv()
        self._funds: list = []
        self._holdings_loaded = False
        self._heatmap_canvas = None
        self._heatmap_fig = None
        self._fund_trends_canvas = None
        self._fund_trends_fig = None
        self._movers_fetching = False
        self._heatmap_job = 0
        self._trends_job = 0
        self._live_poll_after_id: str | None = None
        self._portfolio_job = 0
        self._portfolio_sheet_job = 0
        self._portfolio_live_on_show = False
        # Trees whose stripe/P&L tag colors must be re-applied on a theme switch.
        self._striped_trees: list[tuple[ttk.Treeview, bool]] = []

        self._build_ui()
        self.refresh_funds(clear_caches=False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_live_quote_poll()
        try:
            from reports.portfolio_data import prewarm_sheet_cache

            prewarm_sheet_cache()
        except Exception:
            pass

    def _build_ui(self) -> None:
        self.status_var = tk.StringVar(value="Ready")

        # grid, not pack: a tall tab (e.g. the Fund trends chart) can request
        # more height than the window has. With pack, a fill=BOTH/expand=True
        # notebook competing for space against a later fixed-height sibling
        # doesn't just clip that sibling under pressure -- it unmaps it
        # entirely, so the status bar (and the theme picker living in it)
        # would vanish outright on those tabs. grid's row 0 (weight=1) absorbs
        # the pressure instead; row 1 (weight=0) always keeps its space.
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        self.tab_trades = ttk.Frame(self.notebook, padding=8)
        self.tab_portfolio = ttk.Frame(self.notebook, padding=8)
        self.tab_ledger_detail = ttk.Frame(self.notebook, padding=8)
        self.tab_fund_holdings = ttk.Frame(self.notebook, padding=8)
        self.tab_heatmap = ttk.Frame(self.notebook, padding=8)
        self.tab_movers = ttk.Frame(self.notebook, padding=8)
        self.tab_balances = ttk.Frame(self.notebook, padding=8)
        self.tab_fund_trends = ttk.Frame(self.notebook, padding=8)
        self.tab_net_worth = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.tab_trades, text="Record trade")
        self.notebook.add(self.tab_portfolio, text="Current portfolio")
        self.notebook.add(self.tab_ledger_detail, text="Ledger detail")
        self.notebook.add(self.tab_fund_holdings, text="Holdings by fund")
        self.notebook.add(self.tab_balances, text="Fund balances")
        self.notebook.add(self.tab_fund_trends, text="Fund trends")
        self.notebook.add(self.tab_net_worth, text="Net worth")
        self.notebook.add(self.tab_heatmap, text="Heat map")
        self.notebook.add(self.tab_movers, text="Market movers")

        self._build_trades_tab()
        self._build_portfolio_tab()
        self._build_ledger_detail_tab()
        self._build_fund_holdings_tab()
        self._build_balances_tab()
        self._build_fund_trends_tab()
        self._build_net_worth_tab()
        self._build_heatmap_tab()
        self._build_movers_tab()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        status_row = ttk.Frame(self)
        status_row.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        ttk.Label(status_row, textvariable=self.status_var, style="Status.TLabel", anchor=tk.W).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        ttk.Label(status_row, text="Theme", style="Muted.TLabel").pack(side=tk.LEFT, padx=(8, 4))
        self.theme_var = tk.StringVar(value=_THEME_LABEL_BY_NAME.get(self._theme_name, _THEME_CHOICES[0][0]))
        theme_cb = ttk.Combobox(
            status_row,
            textvariable=self.theme_var,
            values=[label for label, _name in _THEME_CHOICES],
            width=18,
            state="readonly",
        )
        theme_cb.pack(side=tk.LEFT)
        theme_cb.bind("<<ComboboxSelected>>", self._on_theme_selected)

    def _on_theme_selected(self, _event=None) -> None:
        label = self.theme_var.get()
        theme_name = next((name for lbl, name in _THEME_CHOICES if lbl == label), _DEFAULT_THEME_NAME)
        if theme_name == self._theme_name:
            return
        self._theme_name = theme_name
        _apply_theme(self, theme_name)

        # Tag colors are baked in per-tree at configure time; re-apply for the new theme.
        for tree, avg_colors in self._striped_trees:
            _setup_tree_stripes(tree, avg_colors=avg_colors)

        settings = _load_gui_settings()
        settings["theme"] = theme_name
        _save_gui_settings(settings)

        # Repaint the currently visible chart immediately; other chart tabs pick
        # up the new palette next time they're refreshed (see chart_palette()).
        try:
            current = self.notebook.tabs()[self.notebook.index(self.notebook.select())]
        except tk.TclError:
            current = None
        if current == str(self.tab_fund_trends) and self._fund_trends_fig is not None:
            self.refresh_fund_trends_chart()
        elif current == str(self.tab_heatmap) and self._heatmap_fig is not None:
            self.refresh_heatmap(use_live=False)

        self.status_var.set(f"Theme changed to {label}.")

    def _build_trades_tab(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = self.tab_trades

        fund_box = ttk.LabelFrame(root, text="New fund (optional)", padding=8)
        fund_box.pack(fill=tk.X, **{"pady": (0, 10)})

        ff = ttk.Frame(fund_box)
        ff.pack(fill=tk.X)
        ttk.Label(ff, text="Name").grid(row=0, column=0, sticky=tk.W, **pad)
        self.fund_name_var = tk.StringVar()
        ttk.Entry(ff, textvariable=self.fund_name_var, width=22).grid(row=0, column=1, **pad)

        ttk.Label(ff, text="Owner").grid(row=0, column=2, sticky=tk.W, **pad)
        self.fund_owner_var = tk.StringVar(value=OWNER_A)
        ttk.Combobox(ff, textvariable=self.fund_owner_var, values=_OWNERS, width=12, state="readonly").grid(
            row=0, column=3, **pad
        )

        ttk.Label(ff, text="Opening cash").grid(row=0, column=4, sticky=tk.W, **pad)
        self.fund_cash_var = tk.StringVar(value="0")
        ttk.Entry(ff, textvariable=self.fund_cash_var, width=14).grid(row=0, column=5, **pad)

        ttk.Label(ff, text="Currency").grid(row=0, column=6, sticky=tk.W, **pad)
        self.fund_currency_var = tk.StringVar(value="USD")
        ttk.Combobox(ff, textvariable=self.fund_currency_var, values=("USD", "AUD"), width=6, state="readonly").grid(
            row=0, column=7, **pad
        )

        ttkb.Button(ff, text="Create fund", command=self.create_fund, bootstyle="primary").grid(
            row=0, column=8, **pad
        )

        trade_box = ttk.LabelFrame(root, text="Record trade", padding=10)
        trade_box.pack(fill=tk.X, **{"pady": (0, 10)})

        row1 = ttk.Frame(trade_box)
        row1.pack(fill=tk.X)
        ttk.Label(row1, text="Fund").pack(side=tk.LEFT, **pad)
        self.trade_fund_var = tk.StringVar()
        self.fund_combo = ttk.Combobox(row1, textvariable=self.trade_fund_var, width=28, state="readonly")
        self.fund_combo.pack(side=tk.LEFT, **pad)
        ttkb.Button(row1, text="Refresh funds", command=self.refresh_funds, bootstyle="info").pack(
            side=tk.LEFT, **pad
        )

        row2 = ttk.Frame(trade_box)
        row2.pack(fill=tk.X)
        ttk.Label(row2, text="Trade date").pack(side=tk.LEFT, **pad)
        self.trade_date_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.trade_date_var, width=14).pack(side=tk.LEFT, **pad)
        ttk.Label(row2, text="(blank = today)", style="Muted.TLabel").pack(side=tk.LEFT, **pad)

        ttk.Label(row2, text="Instrument").pack(side=tk.LEFT, **pad)
        self.instrument_var = tk.StringVar(value="stock")
        inst_cb = ttk.Combobox(
            row2,
            textvariable=self.instrument_var,
            values=("stock", "option"),
            width=10,
            state="readonly",
        )
        inst_cb.pack(side=tk.LEFT, **pad)
        inst_cb.bind("<<ComboboxSelected>>", lambda _: self._sync_instrument_form())

        self._trade_ticker_label = ttk.Label(row2, text="Ticker")
        self._trade_ticker_label.pack(side=tk.LEFT, **pad)
        self.ticker_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.ticker_var, width=12).pack(side=tk.LEFT, **pad)

        self.option_frame = ttk.Frame(trade_box)
        self.option_frame.pack(fill=tk.X)
        opt_row = ttk.Frame(self.option_frame)
        opt_row.pack(fill=tk.X)
        ttk.Label(opt_row, text="Expiry").pack(side=tk.LEFT, **pad)
        self.opt_expiry_var = tk.StringVar()
        ttk.Entry(opt_row, textvariable=self.opt_expiry_var, width=12).pack(side=tk.LEFT, **pad)
        ttk.Label(opt_row, text="Strike").pack(side=tk.LEFT, **pad)
        self.opt_strike_var = tk.StringVar()
        ttk.Entry(opt_row, textvariable=self.opt_strike_var, width=8).pack(side=tk.LEFT, **pad)
        ttk.Label(opt_row, text="Type").pack(side=tk.LEFT, **pad)
        self.opt_put_call_var = tk.StringVar(value="call")
        ttk.Combobox(
            opt_row,
            textvariable=self.opt_put_call_var,
            values=("call", "put"),
            width=6,
            state="readonly",
        ).pack(side=tk.LEFT, **pad)
        ttk.Label(opt_row, text="×100", style="Muted.TLabel").pack(side=tk.LEFT, **pad)
        self.option_frame.pack_forget()

        row3 = ttk.Frame(trade_box)
        self._trade_row3 = row3
        row3.pack(fill=tk.X)
        ttk.Label(row3, text="Side").pack(side=tk.LEFT, **pad)
        self.side_var = tk.StringVar(value="buy")
        for s in _SIDES:
            ttk.Radiobutton(row3, text=s.capitalize(), variable=self.side_var, value=s).pack(side=tk.LEFT, padx=6)

        self.qty_label = ttk.Label(row3, text="Shares")
        self.qty_label.pack(side=tk.LEFT, **pad)
        self.shares_var = tk.StringVar()
        ttk.Entry(row3, textvariable=self.shares_var, width=10).pack(side=tk.LEFT, **pad)

        self.price_label = ttk.Label(row3, text="Price")
        self.price_label.pack(side=tk.LEFT, **pad)
        self.price_var = tk.StringVar()
        ttk.Entry(row3, textvariable=self.price_var, width=10).pack(side=tk.LEFT, **pad)

        ttk.Label(row3, text="Fees").pack(side=tk.LEFT, **pad)
        self.fees_var = tk.StringVar(value="0")
        ttk.Entry(row3, textvariable=self.fees_var, width=8).pack(side=tk.LEFT, **pad)

        row4 = ttk.Frame(trade_box)
        row4.pack(fill=tk.X)
        ttk.Label(row4, text="Notes").pack(side=tk.LEFT, **pad)
        self.notes_var = tk.StringVar()
        ttk.Entry(row4, textvariable=self.notes_var, width=60).pack(side=tk.LEFT, fill=tk.X, expand=True, **pad)

        btn_row = ttk.Frame(trade_box)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        ttkb.Button(btn_row, text="Save trade", command=self.save_trade, bootstyle="primary").pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_row, text="Clear form", command=self.clear_trade_form).pack(side=tk.LEFT, padx=4)

        list_box = ttk.LabelFrame(root, text="Trades for selected fund", padding=8)
        list_box.pack(fill=tk.BOTH, expand=True)

        cols = ("id", "date", "type", "side", "ticker", "shares", "price", "fees")
        self.trade_tree = ttk.Treeview(list_box, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (40, 90, 56, 50, 120, 70, 80, 60)):
            self.trade_tree.heading(c, text=c.capitalize())
            self.trade_tree.column(c, width=w, anchor=tk.CENTER if c not in ("ticker",) else tk.W)
        scroll = ttk.Scrollbar(list_box, orient=tk.VERTICAL, command=self.trade_tree.yview)
        self.trade_tree.configure(yscrollcommand=scroll.set)
        self.trade_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.fund_combo.bind("<<ComboboxSelected>>", lambda _: self.refresh_trade_list())

    def _build_portfolio_tab(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = self.tab_portfolio

        filt = ttk.Frame(root)
        filt.pack(fill=tk.X)
        ttk.Label(filt, text="Owner").pack(side=tk.LEFT, **pad)
        self.port_owner_var = tk.StringVar(value=OWNER_A)
        owner_cb = ttk.Combobox(
            filt, textvariable=self.port_owner_var, values=_PORT_OWNERS, width=14, state="readonly"
        )
        owner_cb.pack(side=tk.LEFT, **pad)
        owner_cb.bind("<<ComboboxSelected>>", lambda _: self._on_port_owner_changed())

        ttk.Label(filt, text="Fund").pack(side=tk.LEFT, **pad)
        self.port_fund_var = tk.StringVar(value="All")
        self.port_fund_combo = ttk.Combobox(
            filt,
            textvariable=self.port_fund_var,
            values=["All"],
            width=18,
            state="readonly",
        )
        self.port_fund_combo.pack(side=tk.LEFT, **pad)
        self.port_fund_combo.bind(
            "<<ComboboxSelected>>",
            lambda _: self.refresh_portfolio_view(),
        )

        ttk.Label(filt, text="Market").pack(side=tk.LEFT, **pad)
        self.port_market_var = tk.StringVar(value="All")
        port_market_cb = ttk.Combobox(
            filt,
            textvariable=self.port_market_var,
            values=_PORT_MARKETS,
            width=14,
            state="readonly",
        )
        port_market_cb.pack(side=tk.LEFT, **pad)
        port_market_cb.bind("<<ComboboxSelected>>", lambda _: self.refresh_portfolio_view())

        ttkb.Button(filt, text="Refresh portfolio (live prices)", command=lambda: self.refresh_portfolio_view(use_live=True), bootstyle="info").pack(
            side=tk.LEFT, **pad
        )

        self.port_summary_var = tk.StringVar(
            value="Open this tab or click Refresh portfolio for live prices."
        )
        ttk.Label(root, textvariable=self.port_summary_var, wraplength=820).pack(fill=tk.X, pady=(4, 8))

        from reports.portfolio_cards import PortfolioCardsView

        sheet_box = ttk.LabelFrame(
            root,
            text="Holdings (trade ledger — amber border = share count mismatch vs sheet)",
            padding=0,
        )
        sheet_box.pack(fill=tk.BOTH, expand=True)
        self.portfolio_cards = PortfolioCardsView(sheet_box)
        self.portfolio_cards.pack(fill=tk.BOTH, expand=True)

    def _build_ledger_detail_tab(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = self.tab_ledger_detail

        ttk.Label(
            root,
            text="Uses Owner and Fund from the Current portfolio tab.",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(0, 6))

        btn_row = ttk.Frame(root)
        btn_row.pack(fill=tk.X, pady=(0, 6))
        ttkb.Button(
            btn_row,
            text="Refresh ledger detail",
            command=lambda: self.refresh_ledger_detail_view(use_live=True),
            bootstyle="info",
        ).pack(side=tk.LEFT, **pad)

        self.ledger_summary_var = tk.StringVar(
            value="Select Owner and Fund on Current portfolio, then refresh."
        )
        ttk.Label(root, textvariable=self.ledger_summary_var, wraplength=820).pack(
            fill=tk.X, pady=(0, 8)
        )

        book_box = ttk.LabelFrame(root, text="Trade ledger positions (authoritative)", padding=6)
        book_box.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        bcols = ("ticker", "shares", "avg", "mark", "value", "unreal", "realized")
        self.book_tree = ttk.Treeview(book_box, columns=bcols, show="headings", height=12)
        for c, w, h in zip(
            bcols,
            (90, 70, 80, 80, 90, 90, 80),
            ("Ticker", "Shares", "Avg cost", "Mark", "Mkt value", "Unrealized P/L", "Realized P/L"),
        ):
            self.book_tree.heading(c, text=h)
            self.book_tree.column(c, width=w, anchor=tk.E if c != "ticker" else tk.W)
        _setup_tree_stripes(self.book_tree, avg_colors=True)
        self._striped_trees.append((self.book_tree, True))
        bscroll = ttk.Scrollbar(book_box, orient=tk.VERTICAL, command=self.book_tree.yview)
        self.book_tree.configure(yscrollcommand=bscroll.set)
        self.book_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bscroll.pack(side=tk.RIGHT, fill=tk.Y)

        disc_box = ttk.LabelFrame(root, text="Share count discrepancies (ledger vs sheet)", padding=6)
        disc_box.pack(fill=tk.BOTH, expand=True)

        dcols = ("ticker", "ledger", "sheet", "delta", "scope")
        self.disc_tree = ttk.Treeview(disc_box, columns=dcols, show="headings", height=6)
        for c, w, h in zip(
            dcols,
            (72, 88, 88, 72, 220),
            ("Ticker", "Ledger", "Sheet", "Δ", "Scope"),
        ):
            self.disc_tree.heading(c, text=h)
            self.disc_tree.column(c, width=w, anchor=tk.E if c != "ticker" else tk.W)
        self.disc_tree.tag_configure("disc", background="#3d2c00", foreground="#f0c674")
        dscroll = ttk.Scrollbar(disc_box, orient=tk.VERTICAL, command=self.disc_tree.yview)
        self.disc_tree.configure(yscrollcommand=dscroll.set)
        self.disc_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dscroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_fund_holdings_tab(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = self.tab_fund_holdings

        filt = ttk.Frame(root)
        filt.pack(fill=tk.X)
        ttk.Label(filt, text="Owner").pack(side=tk.LEFT, **pad)
        self.fh_owner_var = tk.StringVar(value="All")
        fh_owner_cb = ttk.Combobox(
            filt, textvariable=self.fh_owner_var, values=_PORT_OWNERS, width=14, state="readonly"
        )
        fh_owner_cb.pack(side=tk.LEFT, **pad)
        fh_owner_cb.bind("<<ComboboxSelected>>", lambda _: self.refresh_fund_holdings_view())

        ttk.Label(filt, text="Market").pack(side=tk.LEFT, **pad)
        self.fh_market_var = tk.StringVar(value="All")
        fh_market_cb = ttk.Combobox(
            filt, textvariable=self.fh_market_var, values=_PORT_MARKETS, width=14, state="readonly"
        )
        fh_market_cb.pack(side=tk.LEFT, **pad)
        fh_market_cb.bind("<<ComboboxSelected>>", lambda _: self.refresh_fund_holdings_view())

        ttk.Label(filt, text="Ticker").pack(side=tk.LEFT, **pad)
        self.fh_ticker_var = tk.StringVar(value="")
        fh_ticker_entry = ttk.Entry(filt, textvariable=self.fh_ticker_var, width=10)
        fh_ticker_entry.pack(side=tk.LEFT, **pad)
        fh_ticker_entry.bind("<Return>", lambda _: self.refresh_fund_holdings_view())

        ttkb.Button(
            filt,
            text="Refresh (live prices)",
            command=lambda: self.refresh_fund_holdings_view(use_live=True),
            bootstyle="info",
        ).pack(side=tk.LEFT, **pad)

        self.fh_summary_var = tk.StringVar(
            value="All prices in USD · click a column header to sort · same ticker across funds shows as separate rows."
        )
        ttk.Label(root, textvariable=self.fh_summary_var, wraplength=880).pack(fill=tk.X, pady=(4, 8))

        box = ttk.LabelFrame(root, text="Holdings by fund", padding=6)
        box.pack(fill=tk.BOTH, expand=True)

        fcols = ("owner", "fund", "ccy", "ticker", "shares", "avg", "mark", "value", "unreal", "pct")
        self.fund_holdings_tree = ttk.Treeview(box, columns=fcols, show="headings", height=20)
        headers = (
            ("owner", 90, "Owner", tk.W),
            ("fund", 130, "Fund", tk.W),
            ("ccy", 56, "Fund Ccy", tk.CENTER),
            ("ticker", 80, "Ticker", tk.W),
            ("shares", 80, "Shares", tk.E),
            ("avg", 100, "Avg cost (USD)", tk.E),
            ("mark", 100, "Mark (USD)", tk.E),
            ("value", 100, "Value (USD)", tk.E),
            ("unreal", 120, "Unrealized P/L (USD)", tk.E),
            ("pct", 80, "% of fund", tk.E),
        )
        for c, w, h, anchor in headers:
            self.fund_holdings_tree.heading(
                c, text=h, command=lambda col=c: self._sort_fund_holdings_tree(col)
            )
            self.fund_holdings_tree.column(c, width=w, anchor=anchor)
        _setup_tree_stripes(self.fund_holdings_tree, avg_colors=True)
        self._striped_trees.append((self.fund_holdings_tree, True))
        fscroll = ttk.Scrollbar(box, orient=tk.VERTICAL, command=self.fund_holdings_tree.yview)
        self.fund_holdings_tree.configure(yscrollcommand=fscroll.set)
        self.fund_holdings_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        fscroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_heatmap_tab(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = self.tab_heatmap

        filt = ttk.Frame(root)
        filt.pack(fill=tk.X)
        ttk.Label(filt, text="Owner").pack(side=tk.LEFT, **pad)
        self.hm_owner_var = tk.StringVar(value="All")
        hm_owner_cb = ttk.Combobox(
            filt,
            textvariable=self.hm_owner_var,
            values=("All",) + _OWNERS,
            width=14,
            state="readonly",
        )
        hm_owner_cb.pack(side=tk.LEFT, **pad)
        hm_owner_cb.bind("<<ComboboxSelected>>", lambda _: self._on_heatmap_owner_changed())

        ttk.Label(filt, text="Fund").pack(side=tk.LEFT, **pad)
        self.hm_fund_var = tk.StringVar(value="All")
        self.hm_fund_combo = ttk.Combobox(
            filt, textvariable=self.hm_fund_var, width=28, state="readonly"
        )
        self.hm_fund_combo.pack(side=tk.LEFT, **pad)
        self.hm_fund_combo.bind("<<ComboboxSelected>>", lambda _: self.refresh_heatmap())

        ttk.Label(filt, text="Colour by").pack(side=tk.LEFT, **pad)
        self.hm_color_var = tk.StringVar(value="Overall P/L")
        hm_color_cb = ttk.Combobox(
            filt,
            textvariable=self.hm_color_var,
            values=("Overall P/L", "Day P/L"),
            width=14,
            state="readonly",
        )
        hm_color_cb.pack(side=tk.LEFT, **pad)
        hm_color_cb.bind("<<ComboboxSelected>>", lambda _: self.refresh_heatmap())

        ttkb.Button(filt, text="Refresh (live prices)", command=lambda: self.refresh_heatmap(use_live=True), bootstyle="info").pack(
            side=tk.LEFT, **pad
        )

        self.hm_summary_var = tk.StringVar(
            value="Tile size = position value · colour = P/L % · total shown in USD"
        )
        ttk.Label(root, textvariable=self.hm_summary_var, wraplength=880).pack(fill=tk.X, pady=(2, 6))

        self.hm_chart_frame = ttk.Frame(root)
        self.hm_chart_frame.pack(fill=tk.BOTH, expand=True)

        self._sync_heatmap_fund_filter()

    def _build_balances_tab(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = self.tab_balances

        top = ttk.Frame(root)
        top.pack(fill=tk.X)
        ttkb.Button(top, text="Refresh", command=self.refresh_fund_balances, bootstyle="info").pack(
            side=tk.LEFT, **pad
        )

        self.bal_summary_var = tk.StringVar(
            value="Manual balance overrides the ledger total when set · ledger uses trade history + mark prices"
        )
        ttk.Label(root, textvariable=self.bal_summary_var, wraplength=880).pack(fill=tk.X, pady=(2, 6))

        list_box = ttk.LabelFrame(root, text="Current fund balances", padding=6)
        list_box.pack(fill=tk.BOTH, expand=True)

        bcols = (
            "fund",
            "owner",
            "currency",
            "manual_nav",
            "manual_cash",
            "manual_pos",
            "as_of",
            "ledger_nav",
            "ledger_cash",
        )
        self.balance_tree = ttk.Treeview(list_box, columns=bcols, show="headings", height=12)
        headers = {
            "fund": "Fund",
            "owner": "Owner",
            "currency": "Cur",
            "manual_nav": "Manual total",
            "manual_cash": "Manual cash",
            "manual_pos": "Manual positions",
            "as_of": "As of",
            "ledger_nav": "Ledger total",
            "ledger_cash": "Ledger cash",
        }
        widths = {
            "fund": 140,
            "owner": 72,
            "currency": 44,
            "manual_nav": 108,
            "manual_cash": 100,
            "manual_pos": 108,
            "as_of": 88,
            "ledger_nav": 108,
            "ledger_cash": 100,
        }
        for c in bcols:
            anchor = tk.W if c in ("fund", "owner") else tk.E
            self.balance_tree.heading(c, text=headers[c])
            self.balance_tree.column(c, width=widths[c], anchor=anchor, stretch=c == "fund")
        bscroll = ttk.Scrollbar(list_box, orient=tk.VERTICAL, command=self.balance_tree.yview)
        self.balance_tree.configure(yscrollcommand=bscroll.set)
        self.balance_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.balance_tree.bind("<<TreeviewSelect>>", self._on_balance_row_selected)

        edit_box = ttk.LabelFrame(root, text="Update balance manually", padding=8)
        edit_box.pack(fill=tk.X, pady=(8, 0))

        er1 = ttk.Frame(edit_box)
        er1.pack(fill=tk.X)
        ttk.Label(er1, text="Fund").pack(side=tk.LEFT, **pad)
        self.bal_fund_var = tk.StringVar()
        self.bal_fund_combo = ttk.Combobox(
            er1, textvariable=self.bal_fund_var, width=28, state="readonly"
        )
        self.bal_fund_combo.pack(side=tk.LEFT, **pad)
        self.bal_fund_combo.bind("<<ComboboxSelected>>", self._load_balance_form_for_fund)

        ttk.Label(er1, text="As of date").pack(side=tk.LEFT, **pad)
        self.bal_date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(er1, textvariable=self.bal_date_var, width=12).pack(side=tk.LEFT, **pad)

        er2 = ttk.Frame(edit_box)
        er2.pack(fill=tk.X)
        ttk.Label(er2, text="Total balance (NAV)").pack(side=tk.LEFT, **pad)
        self.bal_nav_var = tk.StringVar()
        ttk.Entry(er2, textvariable=self.bal_nav_var, width=14).pack(side=tk.LEFT, **pad)

        ttk.Label(er2, text="Cash (optional)").pack(side=tk.LEFT, **pad)
        self.bal_cash_var = tk.StringVar()
        ttk.Entry(er2, textvariable=self.bal_cash_var, width=12).pack(side=tk.LEFT, **pad)

        ttk.Label(er2, text="Positions (optional)").pack(side=tk.LEFT, **pad)
        self.bal_pos_var = tk.StringVar()
        ttk.Entry(er2, textvariable=self.bal_pos_var, width=12).pack(side=tk.LEFT, **pad)

        er3 = ttk.Frame(edit_box)
        er3.pack(fill=tk.X)
        ttk.Label(er3, text="Notes").pack(side=tk.LEFT, **pad)
        self.bal_notes_var = tk.StringVar()
        ttk.Entry(er3, textvariable=self.bal_notes_var, width=50).pack(
            side=tk.LEFT, fill=tk.X, expand=True, **pad
        )

        er4 = ttk.Frame(edit_box)
        er4.pack(fill=tk.X, pady=(4, 0))
        ttkb.Button(
            er4, text="Save balance", command=self.save_manual_fund_balance, bootstyle="primary"
        ).pack(side=tk.LEFT, **pad)
        ttkb.Button(
            er4,
            text="Clear manual balance",
            command=self.clear_manual_fund_balance,
            bootstyle="danger",
        ).pack(side=tk.LEFT, **pad)

    def _build_fund_trends_tab(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = self.tab_fund_trends

        filt = ttk.Frame(root)
        filt.pack(fill=tk.X)
        ttk.Label(filt, text="Owner").pack(side=tk.LEFT, **pad)
        self.trends_owner_var = tk.StringVar(value="All")
        trends_owner_cb = ttk.Combobox(
            filt,
            textvariable=self.trends_owner_var,
            values=("All",) + _OWNERS,
            width=14,
            state="readonly",
        )
        trends_owner_cb.pack(side=tk.LEFT, **pad)
        trends_owner_cb.bind("<<ComboboxSelected>>", lambda _: self._on_trends_owner_changed())

        ttk.Label(filt, text="Fund").pack(side=tk.LEFT, **pad)
        self.trends_fund_var = tk.StringVar(value="All")
        self.trends_fund_combo = ttk.Combobox(
            filt, textvariable=self.trends_fund_var, width=28, state="readonly"
        )
        self.trends_fund_combo.pack(side=tk.LEFT, **pad)
        self.trends_fund_combo.bind("<<ComboboxSelected>>", lambda _: self.refresh_fund_trends_chart())

        ttk.Label(filt, text="Location").pack(side=tk.LEFT, **pad)
        self.trends_location_var = tk.StringVar(value="All")
        trends_loc_cb = ttk.Combobox(
            filt,
            textvariable=self.trends_location_var,
            values=_FUND_LOCATIONS,
            width=18,
            state="readonly",
        )
        trends_loc_cb.pack(side=tk.LEFT, **pad)
        trends_loc_cb.bind("<<ComboboxSelected>>", lambda _: self.refresh_fund_trends_chart())

        ttkb.Button(filt, text="Refresh", command=self.refresh_fund_trends_chart, bootstyle="info").pack(
            side=tk.LEFT, **pad
        )

        ttk.Label(filt, text="Show").pack(side=tk.LEFT, **pad)
        self.trends_mode_var = tk.StringVar(value="Monthly statements")
        trends_mode_cb = ttk.Combobox(
            filt,
            textvariable=self.trends_mode_var,
            values=(
                "Monthly statements",
                "From share transactions (month-end)",
                "Include manual balances",
            ),
            width=28,
            state="readonly",
        )
        trends_mode_cb.pack(side=tk.LEFT, **pad)
        trends_mode_cb.bind("<<ComboboxSelected>>", lambda _: self.refresh_fund_trends_chart())

        self.trends_summary_var = tk.StringVar(
            value="Fund value (NAV) from monthly Fidelity statement snapshots"
        )
        ttk.Label(root, textvariable=self.trends_summary_var, wraplength=880).pack(fill=tk.X, pady=(2, 6))

        self.trends_chart_frame = ttk.Frame(root)
        self.trends_chart_frame.pack(fill=tk.BOTH, expand=True)

        self._sync_trends_fund_filter()

    def _build_net_worth_tab(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = self.tab_net_worth

        filt = ttk.Frame(root)
        filt.pack(fill=tk.X)
        ttk.Label(filt, text="Owner").pack(side=tk.LEFT, **pad)
        self.nw_owner_var = tk.StringVar(value=OWNER_A)
        nw_owner_cb = ttk.Combobox(
            filt, textvariable=self.nw_owner_var, values=_OWNERS, width=12, state="readonly"
        )
        nw_owner_cb.pack(side=tk.LEFT, **pad)
        nw_owner_cb.bind("<<ComboboxSelected>>", lambda _: self.refresh_net_worth(use_live=False))
        ttkb.Button(
            filt, text="Refresh", command=lambda: self.refresh_net_worth(use_live=False), bootstyle="info"
        ).pack(side=tk.LEFT, **pad)
        ttkb.Button(
            filt,
            text="Refresh (live prices + FX)",
            command=lambda: self.refresh_net_worth(use_live=True),
            bootstyle="info",
        ).pack(side=tk.LEFT, **pad)

        self.nw_summary_var = tk.StringVar(
            value="Funds use the manual balance when set, otherwise the live ledger total."
        )
        ttk.Label(root, textvariable=self.nw_summary_var, wraplength=880).pack(fill=tk.X, pady=(2, 2))

        total_card = ttk.Frame(root, style="TotalCard.TFrame", padding=(14, 10))
        total_card.pack(fill=tk.X, pady=(0, 8))
        self.nw_total_var = tk.StringVar(value="Total: —")
        self.nw_total_label = ttk.Label(
            total_card,
            textvariable=self.nw_total_var,
            style="TotalGood.TLabel",
            font=("TkDefaultFont", 26, "bold"),
        )
        self.nw_total_label.pack(anchor=tk.W)

        list_box = ttk.LabelFrame(root, text="Breakdown", padding=6)
        list_box.pack(fill=tk.BOTH, expand=True)

        ncols = ("item", "kind", "currency", "local_amount", "usd_amount", "as_of", "source")
        self.net_worth_tree = ttk.Treeview(list_box, columns=ncols, show="headings", height=12)
        headers = {
            "item": "Item",
            "kind": "Kind",
            "currency": "Cur",
            "local_amount": "Local amount",
            "usd_amount": "USD",
            "as_of": "As of",
            "source": "Source",
        }
        widths = {
            "item": 220,
            "kind": 60,
            "currency": 48,
            "local_amount": 120,
            "usd_amount": 120,
            "as_of": 90,
            "source": 110,
        }
        for c in ncols:
            anchor = tk.W if c in ("item", "kind", "source") else tk.E
            self.net_worth_tree.heading(c, text=headers[c])
            self.net_worth_tree.column(c, width=widths[c], anchor=anchor, stretch=c == "item")
        nwscroll = ttk.Scrollbar(list_box, orient=tk.VERTICAL, command=self.net_worth_tree.yview)
        self.net_worth_tree.configure(yscrollcommand=nwscroll.set)
        self.net_worth_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        nwscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.net_worth_tree.bind("<<TreeviewSelect>>", self._on_other_asset_row_selected)

        edit_box = ttk.LabelFrame(root, text="Other assets (bank accounts, property, ...)", padding=8)
        edit_box.pack(fill=tk.X, pady=(8, 0))

        er1 = ttk.Frame(edit_box)
        er1.pack(fill=tk.X)
        ttk.Label(er1, text="Asset type").pack(side=tk.LEFT, **pad)
        from ledger.trades_db import OWNER_OTHER_ASSET_TYPES

        self.nw_asset_type_var = tk.StringVar(value=OWNER_OTHER_ASSET_TYPES[0])
        ttk.Combobox(
            er1,
            textvariable=self.nw_asset_type_var,
            values=OWNER_OTHER_ASSET_TYPES,
            width=20,
            state="normal",
        ).pack(side=tk.LEFT, **pad)

        ttk.Label(er1, text="Amount").pack(side=tk.LEFT, **pad)
        self.nw_asset_amount_var = tk.StringVar()
        ttk.Entry(er1, textvariable=self.nw_asset_amount_var, width=14).pack(side=tk.LEFT, **pad)

        ttk.Label(er1, text="Currency").pack(side=tk.LEFT, **pad)
        self.nw_asset_currency_var = tk.StringVar(value="USD")
        ttk.Combobox(
            er1,
            textvariable=self.nw_asset_currency_var,
            values=_OTHER_ASSET_CURRENCIES,
            width=6,
            state="readonly",
        ).pack(side=tk.LEFT, **pad)

        er2 = ttk.Frame(edit_box)
        er2.pack(fill=tk.X)
        ttk.Label(er2, text="Notes").pack(side=tk.LEFT, **pad)
        self.nw_asset_notes_var = tk.StringVar()
        ttk.Entry(er2, textvariable=self.nw_asset_notes_var, width=50).pack(
            side=tk.LEFT, fill=tk.X, expand=True, **pad
        )

        er3 = ttk.Frame(edit_box)
        er3.pack(fill=tk.X, pady=(4, 0))
        ttkb.Button(er3, text="Save", command=self.save_other_asset, bootstyle="primary").pack(
            side=tk.LEFT, **pad
        )
        ttkb.Button(er3, text="Delete", command=self.delete_other_asset, bootstyle="danger").pack(
            side=tk.LEFT, **pad
        )

    def refresh_net_worth(self, *, use_live: bool = False) -> None:
        from ledger.db import connect_db
        from reports.net_worth import compute_owner_net_worth

        owner = self.nw_owner_var.get().strip()
        self._clear_tree(self.net_worth_tree)
        if not owner:
            return

        conn = connect_db(trades_cli._db_path())
        try:
            result = compute_owner_net_worth(conn, owner, use_live=use_live)
        finally:
            conn.close()

        for line in result.lines:
            self.net_worth_tree.insert(
                "",
                tk.END,
                iid=f"{line.kind}:{line.label}",
                values=(
                    line.label,
                    line.kind,
                    line.currency,
                    _fmt_fund_money(line.amount_local, line.currency),
                    _fmt_fund_money(line.amount_usd, "USD"),
                    line.as_of,
                    line.source,
                ),
            )

        fx_note = " · ".join(
            f"{cur} {rate:.4f}" for cur, rate in sorted(result.fx_rates.items()) if cur not in ("USD", "US")
        )
        mode = "live" if use_live else "cached"
        self.nw_summary_var.set(
            f"{len(result.lines)} item(s) for {_port_owner_label(owner)} · FX ({mode}): {fx_note or 'USD only'} · "
            "funds use the manual balance when set, otherwise the live ledger total"
        )
        self.nw_total_var.set(f"Total ({_port_owner_label(owner)}): {_fmt_fund_money(result.total_usd, 'USD')}")
        self.nw_total_label.configure(
            style="TotalGood.TLabel" if result.total_usd >= 0 else "TotalBad.TLabel"
        )
        self.status_var.set(f"Net worth updated for {_port_owner_label(owner)}.")

    def _on_other_asset_row_selected(self, _event=None) -> None:
        sel = self.net_worth_tree.selection()
        if not sel or not sel[0].startswith("other:"):
            return
        from ledger.db import connect_db
        from ledger.trades_db import fetch_owner_other_assets

        asset_type = sel[0].split(":", 1)[1]
        owner = self.nw_owner_var.get().strip()
        conn = connect_db(trades_cli._db_path())
        try:
            assets = fetch_owner_other_assets(conn, owner)
        finally:
            conn.close()
        match = next((a for a in assets if a.asset_type == asset_type), None)
        if match:
            self.nw_asset_type_var.set(match.asset_type)
            self.nw_asset_amount_var.set(f"{match.amount:g}")
            self.nw_asset_currency_var.set(match.currency)
            self.nw_asset_notes_var.set(match.notes or "")

    def save_other_asset(self) -> None:
        from ledger.db import connect_db
        from ledger.trades_db import upsert_owner_other_asset

        owner = self.nw_owner_var.get().strip()
        asset_type = self.nw_asset_type_var.get().strip()
        if not owner or not asset_type:
            messagebox.showwarning("Net worth", "Select an owner and asset type.")
            return
        try:
            amount = self._parse_optional_amount(self.nw_asset_amount_var.get())
            if amount is None:
                raise ValueError("Enter an amount.")
        except ValueError as e:
            messagebox.showerror("Net worth", str(e))
            return
        currency = self.nw_asset_currency_var.get().strip() or "USD"
        notes = self.nw_asset_notes_var.get().strip() or None

        conn = connect_db(trades_cli._db_path())
        try:
            upsert_owner_other_asset(conn, owner, asset_type, amount, currency=currency, notes=notes)
        finally:
            conn.close()

        self.status_var.set(f"Saved {asset_type} for {_port_owner_label(owner)}.")
        self.refresh_net_worth(use_live=False)

    def delete_other_asset(self) -> None:
        from ledger.db import connect_db
        from ledger.trades_db import delete_owner_other_asset

        owner = self.nw_owner_var.get().strip()
        asset_type = self.nw_asset_type_var.get().strip()
        if not owner or not asset_type:
            return
        conn = connect_db(trades_cli._db_path())
        try:
            removed = delete_owner_other_asset(conn, owner, asset_type)
        finally:
            conn.close()
        if removed:
            self.status_var.set(f"Removed {asset_type} for {_port_owner_label(owner)}.")
            self.nw_asset_amount_var.set("")
            self.nw_asset_notes_var.set("")
            self.refresh_net_worth(use_live=False)

    def _live_quote_poll_ms(self) -> int:
        try:
            seconds = int(os.getenv("LIVE_QUOTES_POLL_SEC", "60"))
        except ValueError:
            seconds = 60
        return max(0, seconds) * 1000

    def _schedule_live_quote_poll(self) -> None:
        if self._live_poll_after_id is not None:
            self.after_cancel(self._live_poll_after_id)
            self._live_poll_after_id = None
        interval_ms = self._live_quote_poll_ms()
        if interval_ms <= 0:
            return
        self._live_poll_after_id = self.after(interval_ms, self._on_live_quote_poll)

    def _on_live_quote_poll(self) -> None:
        self._live_poll_after_id = None
        try:
            from ledger.market_calendar import any_exchange_open_now
            from ledger.quotes import clear_live_quotes_cache, live_quotes_enabled

            if live_quotes_enabled() and any_exchange_open_now():
                clear_live_quotes_cache()
                self._refresh_active_price_tab(use_live=True)
        finally:
            self._schedule_live_quote_poll()

    def _active_price_tab(self) -> str | None:
        try:
            tab = self.notebook.index(self.notebook.select())
            tab_id = self.notebook.tabs()[tab]
        except tk.TclError:
            return None
        if tab_id == str(self.tab_portfolio):
            return "portfolio"
        if tab_id == str(self.tab_heatmap):
            return "heatmap"
        if tab_id == str(self.tab_movers):
            return "movers"
        return None

    def _refresh_active_price_tab(self, *, use_live: bool) -> None:
        active = self._active_price_tab()
        if active == "portfolio":
            self.refresh_portfolio_view(use_live=use_live)
        elif active == "heatmap":
            self.refresh_heatmap(use_live=use_live)
        elif active == "movers":
            self.refresh_market_movers(force=use_live)
        if active is not None and use_live:
            self.status_var.set(f"Live prices refreshed · {datetime.now():%H:%M}")

    def _on_close(self) -> None:
        if self._live_poll_after_id is not None:
            self.after_cancel(self._live_poll_after_id)
            self._live_poll_after_id = None
        self.destroy()

    def _on_tab_changed(self, _event=None) -> None:
        try:
            tab = self.notebook.index(self.notebook.select())
        except tk.TclError:
            return
        if self.notebook.tabs()[tab] == str(self.tab_balances):
            self.refresh_fund_balances()
        elif self.notebook.tabs()[tab] == str(self.tab_fund_trends):
            self.refresh_fund_trends_chart()
        elif self.notebook.tabs()[tab] == str(self.tab_net_worth):
            self.refresh_net_worth(use_live=False)
        elif self.notebook.tabs()[tab] == str(self.tab_heatmap):
            from ledger.quotes import live_quotes_enabled

            self.refresh_heatmap(use_live=live_quotes_enabled())
        elif self.notebook.tabs()[tab] == str(self.tab_portfolio):
            self._portfolio_live_on_show = True
            self.refresh_portfolio_view(use_live=False)
        elif self.notebook.tabs()[tab] == str(self.tab_ledger_detail):
            self.refresh_ledger_detail_view(use_live=False)
        elif self.notebook.tabs()[tab] == str(self.tab_fund_holdings):
            self.refresh_fund_holdings_view(use_live=False)
        elif self.notebook.tabs()[tab] == str(self.tab_movers):
            self.refresh_market_movers()

    def _build_movers_tab(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root = self.tab_movers

        filt = ttk.Frame(root)
        filt.pack(fill=tk.X)
        ttk.Label(filt, text="Market").pack(side=tk.LEFT, **pad)
        self.movers_market_var = tk.StringVar(value="United States")
        movers_market_cb = ttk.Combobox(
            filt,
            textvariable=self.movers_market_var,
            values=("United States", "Australia"),
            width=18,
            state="readonly",
        )
        movers_market_cb.pack(side=tk.LEFT, **pad)
        movers_market_cb.bind("<<ComboboxSelected>>", lambda _: self.refresh_market_movers(force=True))

        ttkb.Button(
            filt, text="Refresh", command=lambda: self.refresh_market_movers(force=True), bootstyle="info"
        ).pack(side=tk.LEFT, **pad)

        self.movers_summary_var = tk.StringVar(
            value="Top 20 day gainers and losers · major markets only (NYSE/NASDAQ or ASX)"
        )
        ttk.Label(root, textvariable=self.movers_summary_var, wraplength=880).pack(fill=tk.X, pady=(2, 6))

        panes = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True)

        gain_box = ttk.LabelFrame(panes, text="Gainers", padding=4)
        lose_box = ttk.LabelFrame(panes, text="Losers", padding=4)
        panes.add(gain_box, weight=1)
        panes.add(lose_box, weight=1)

        mcols = ("rank", "symbol", "name", "price", "change", "pct", "volume")
        self.movers_gain_tree = self._make_movers_tree(gain_box, mcols)
        self.movers_lose_tree = self._make_movers_tree(lose_box, mcols)

    def _make_movers_tree(self, parent: ttk.LabelFrame, columns: tuple[str, ...]) -> ttk.Treeview:
        headings = {
            "rank": "#",
            "symbol": "Ticker",
            "name": "Name",
            "price": "Price",
            "change": "Change",
            "pct": "Change %",
            "volume": "Volume",
        }
        widths = {
            "rank": 36,
            "symbol": 72,
            "name": 180,
            "price": 88,
            "change": 80,
            "pct": 80,
            "volume": 90,
        }
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=22)
        _setup_tree_stripes(tree)
        self._striped_trees.append((tree, False))
        for col in columns:
            tree.heading(col, text=headings[col])
            anchor = tk.W if col in ("symbol", "name") else tk.E
            tree.column(col, width=widths[col], anchor=anchor, stretch=col == "name")
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def _movers_market_code(self) -> str:
        return "AU" if self.movers_market_var.get().strip().startswith("Austral") else "US"

    @staticmethod
    def _fmt_volume(vol: int | None) -> str:
        if vol is None:
            return "—"
        if vol >= 1_000_000_000:
            return f"{vol / 1_000_000_000:.2f}B"
        if vol >= 1_000_000:
            return f"{vol / 1_000_000:.2f}M"
        if vol >= 1_000:
            return f"{vol / 1_000:.1f}K"
        return f"{vol:,}"

    def refresh_market_movers(self, *, force: bool = False) -> None:
        if self._movers_fetching:
            return
        self._movers_fetching = True
        market = self._movers_market_code()
        market_label = "Australia" if market == "AU" else "United States"
        self.status_var.set(f"Loading {market_label} market movers…")
        self.update_idletasks()

        import threading

        def worker() -> None:
            err: str | None = None
            gainers = []
            losers = []
            try:
                from ledger.market_movers import fetch_market_movers

                gainers, losers = fetch_market_movers(market, force=force)  # type: ignore[arg-type]
            except Exception as e:
                err = str(e)
            self.after(0, lambda: self._apply_market_movers(gainers, losers, market, err))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_market_movers(self, gainers, losers, market: str, error: str | None) -> None:
        self._movers_fetching = False
        if error:
            messagebox.showerror("Market movers", f"Could not load market movers:\n{error}")
            self.status_var.set(f"Market movers error: {error}")
            return

        loc = "AUS" if market == "AU" else "US"
        market_label = "Australia (ASX, large cap)" if market == "AU" else "United States (NYSE & NASDAQ, large cap)"

        for tree, rows in ((self.movers_gain_tree, gainers), (self.movers_lose_tree, losers)):
            for item in tree.get_children():
                tree.delete(item)
            for idx, row in enumerate(rows):
                price = _fmt_money(row.price, loc) if row.price is not None else "—"
                change = _fmt_money(row.change, loc) if row.change is not None else "—"
                pct = _fmt_pct(row.change_pct) if row.change_pct is not None else "—"
                tree.insert(
                    "",
                    tk.END,
                    tags=_tree_row_tags(idx),
                    values=(
                        row.rank,
                        row.symbol,
                        row.name,
                        price,
                        change,
                        pct,
                        self._fmt_volume(row.volume),
                    ),
                )

        updated = datetime.now().strftime("%H:%M")
        self.movers_summary_var.set(
            f"{market_label} · top {len(gainers)} gainers / {len(losers)} losers · "
            f"updated {updated} · Yahoo Finance (delayed)"
        )
        self.status_var.set(f"Market movers updated for {market_label}.")

    def _sync_heatmap_fund_filter(self) -> None:
        owner = self.hm_owner_var.get().strip()
        if owner == "All":
            names = ["All"] + [f.name for f in self._funds]
        else:
            names = ["All"] + [f.name for f in self._funds_for_owner(owner)]
        self.hm_fund_combo["values"] = names
        if self.hm_fund_var.get() not in names:
            self.hm_fund_var.set("All")

    def _on_heatmap_owner_changed(self) -> None:
        self._sync_heatmap_fund_filter()
        self.refresh_heatmap()

    def _heatmap_color_by(self) -> str:
        return "day" if self.hm_color_var.get().strip().startswith("Day") else "overall"

    def refresh_heatmap(self, *, use_live: bool = False) -> None:
        self._heatmap_job += 1
        job = self._heatmap_job

        owner_sel = self.hm_owner_var.get().strip()
        fund_sel = self.hm_fund_var.get().strip()
        owner_arg = None if owner_sel == "All" else owner_sel
        fund_arg = None if fund_sel == "All" else fund_sel
        color_by = self._heatmap_color_by()

        note = "Fetching live prices…" if use_live else "Loading heat map…"
        self.status_var.set(note)
        self.update_idletasks()

        import threading

        def worker() -> None:
            err: str | None = None
            tiles = []
            try:
                from reports.heatmap_data import collect_heatmap_tiles

                tiles = collect_heatmap_tiles(
                    trades_cli._db_path(),
                    owner=owner_arg,
                    fund_name=fund_arg,
                    live=use_live,
                    color_by=color_by,  # type: ignore[arg-type]
                )
            except Exception as e:
                err = str(e)
            self.after(
                0,
                lambda: self._apply_heatmap_result(
                    job, tiles, owner_sel, fund_sel, color_by, use_live, err
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_heatmap_result(
        self,
        job: int,
        tiles,
        owner_sel: str,
        fund_sel: str,
        color_by: str,
        use_live: bool,
        error: str | None,
    ) -> None:
        if job != self._heatmap_job:
            return
        if error:
            messagebox.showerror("Heat map", f"Could not load heat map:\n{error}")
            self.status_var.set(f"Heat map error: {error}")
            return

        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        from reports.heatmap_chart import build_heatmap_figure

        owner_label = "All owners" if owner_sel == "All" else _DISPLAY.get(owner_sel, owner_sel)
        fund_label = "All funds" if fund_sel == "All" else fund_sel
        title = f"{owner_label} · {fund_label}"
        if self._heatmap_canvas is not None:
            self._heatmap_canvas.get_tk_widget().destroy()
            self._heatmap_canvas = None
        if self._heatmap_fig is not None:
            import matplotlib.pyplot as plt

            plt.close(self._heatmap_fig)
            self._heatmap_fig = None

        try:
            self._heatmap_fig = build_heatmap_figure(tiles, title=title, color_by=color_by, palette=chart_palette())  # type: ignore[arg-type]
            self._heatmap_canvas = FigureCanvasTkAgg(self._heatmap_fig, master=self.hm_chart_frame)
            self._heatmap_canvas.draw()
            self._heatmap_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        except Exception as e:
            messagebox.showerror("Heat map", f"Could not draw heat map:\n{e}")
            self.status_var.set(f"Heat map error: {e}")
            return

        try:
            from ledger.quotes import live_quotes_enabled, sum_tiles_value_usd

            total_usd = sum_tiles_value_usd(tiles, force_fx=use_live)
            live_note = " · Yahoo Finance (delayed)" if live_quotes_enabled() else ""
        except ImportError:
            total_usd = sum(t.value for t in tiles)
            live_note = ""
        merged_note = " · tickers merged across funds" if fund_sel == "All" else ""
        color_label = "day P/L %" if color_by == "day" else "unrealized P/L %"
        self.hm_summary_var.set(
            f"{len(tiles)} position(s) · total value ${total_usd:,.0f} USD{merged_note}{live_note} · "
            f"colour = {color_label} · scale −5% … +5%"
        )
        self.status_var.set(f"Heat map updated: {title}.")

    def _funds_for_owner(self, owner: str) -> list:
        if owner.strip().lower() == "all":
            return list(self._funds)
        return [f for f in self._funds if f.owner == owner]

    def _port_fund_filter(self) -> str | None:
        """None = all funds for the selected owner."""
        name = self.port_fund_var.get().strip()
        if not name or name.lower() == "all":
            return None
        return name

    def _sync_port_fund_combo(self) -> None:
        owner = self.port_owner_var.get()
        names = ["All"] + [f.name for f in self._funds_for_owner(owner)]
        self.port_fund_combo["values"] = names
        if names:
            if self.port_fund_var.get() not in names:
                self.port_fund_var.set("All")
        else:
            self.port_fund_var.set("All")

    def _on_port_owner_changed(self) -> None:
        self._sync_port_fund_combo()
        self.refresh_portfolio_view()

    def refresh_funds(self, *, clear_caches: bool = True) -> None:
        from ledger.db import connect_db
        from ledger.trades_db import init_trade_schema, list_funds

        if clear_caches:
            from reports.fund_balance_history import clear_fund_balance_series_cache
            from reports.heatmap_data import clear_heatmap_caches

            clear_fund_balance_series_cache()
            clear_heatmap_caches()

        conn = connect_db(trades_cli._db_path())
        try:
            init_trade_schema(conn)
            self._funds = list_funds(conn)
        finally:
            conn.close()

        names = [f.name for f in self._funds]
        self.fund_combo["values"] = names
        if names:
            if self.trade_fund_var.get() not in names:
                self.trade_fund_var.set(names[0])
            self.refresh_trade_list()
        else:
            self.trade_fund_var.set("")
            self._clear_tree(self.trade_tree)

        self._sync_port_fund_combo()
        self._sync_heatmap_fund_filter()
        self._sync_balance_fund_combo()
        self._sync_trends_fund_filter()
        self.status_var.set(f"{len(names)} fund(s) loaded.")

    def _sync_trends_fund_filter(self) -> None:
        if not hasattr(self, "trends_fund_combo"):
            return
        owner = self.trends_owner_var.get().strip()
        if owner == "All":
            names = ["All"] + [f.name for f in self._funds]
        else:
            names = ["All"] + [f.name for f in self._funds_for_owner(owner)]
        self.trends_fund_combo["values"] = names
        if self.trends_fund_var.get() not in names:
            self.trends_fund_var.set("All")

    def _on_trends_owner_changed(self) -> None:
        self._sync_trends_fund_filter()
        self.refresh_fund_trends_chart()

    def _trends_mode_from_ui(self) -> str:
        sel = self.trends_mode_var.get().strip()
        if sel.startswith("From share"):
            return "transactions"
        if sel.startswith("Include manual"):
            return "manual"
        return "statements"

    def refresh_fund_trends_chart(self) -> None:
        self._trends_job += 1
        job = self._trends_job

        owner_sel = self.trends_owner_var.get().strip()
        fund_sel = self.trends_fund_var.get().strip()
        loc_sel = self.trends_location_var.get().strip()
        trends_mode = self._trends_mode_from_ui()
        owner_arg = None if owner_sel == "All" else owner_sel
        fund_arg = None if fund_sel == "All" else fund_sel

        self.status_var.set("Loading fund trends…")
        self.update_idletasks()

        import threading

        def worker() -> None:
            err: str | None = None
            series = []
            try:
                from reports.fund_balance_history import collect_fund_balance_series

                series = collect_fund_balance_series(
                    trades_cli._db_path(),
                    owner=owner_arg,
                    fund_name=fund_arg,
                    location=loc_sel,
                    trends_mode=trends_mode,  # type: ignore[arg-type]
                )
            except Exception as e:
                err = str(e)
            self.after(
                0,
                lambda: self._apply_fund_trends_result(
                    job, series, owner_sel, fund_sel, loc_sel, trends_mode, err
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_fund_trends_result(
        self,
        job: int,
        series,
        owner_sel: str,
        fund_sel: str,
        loc_sel: str,
        trends_mode: str,
        error: str | None,
    ) -> None:
        if job != self._trends_job:
            return
        if error:
            messagebox.showerror("Fund trends", f"Could not load fund history:\n{error}")
            self.status_var.set(f"Fund trends error: {error}")
            return

        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        from reports.fund_balance_chart import build_fund_balance_figure

        owner_label = "All owners" if owner_sel == "All" else _DISPLAY.get(owner_sel, owner_sel)
        fund_label = "All funds" if fund_sel == "All" else fund_sel
        loc_label = loc_sel if loc_sel else "All"
        title = f"{owner_label} · {fund_label} · {loc_label}"

        if self._fund_trends_canvas is not None:
            self._fund_trends_canvas.get_tk_widget().destroy()
            self._fund_trends_canvas = None
        if self._fund_trends_fig is not None:
            import matplotlib.pyplot as plt

            plt.close(self._fund_trends_fig)
            self._fund_trends_fig = None

        try:
            self._fund_trends_fig = build_fund_balance_figure(
                series,
                title=title,
                value_label="Fund value (NAV)",
                palette=chart_palette(),
            )
            self._fund_trends_canvas = FigureCanvasTkAgg(
                self._fund_trends_fig, master=self.trends_chart_frame
            )
            self._fund_trends_canvas.draw()
            self._fund_trends_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        except Exception as e:
            messagebox.showerror("Fund trends", f"Could not draw chart:\n{e}")
            self.status_var.set(f"Fund trends error: {e}")
            return

        pts = sum(len(s.points) for s in series)
        total_note = ""
        if fund_sel == "All" and len(series) > 1:
            total_note = " · white line = combined total"
        mode_notes = {
            "statements": "monthly broker statement snapshots",
            "transactions": "month-end replay from share trades",
            "manual": "statement snapshots + manual balance overrides",
        }
        mode_note = mode_notes.get(trends_mode, trends_mode)
        self.trends_summary_var.set(
            f"{len(series)} fund line(s) · {pts} monthly point(s) · "
            f"solid = NAV · dashed = deposits & contributions (not asset transfers) · {mode_note}"
            f"{total_note} · drag slider to zoom dates"
        )
        self.status_var.set(f"Fund trends updated: {title}.")

    def _sync_balance_fund_combo(self) -> None:
        if not hasattr(self, "bal_fund_combo"):
            return
        names = [f.name for f in self._funds]
        self.bal_fund_combo["values"] = names
        if names and self.bal_fund_var.get() not in names:
            self.bal_fund_var.set(names[0])

    def _parse_optional_amount(self, raw: str) -> float | None:
        text = raw.strip().replace(",", "").replace("$", "")
        if not text:
            return None
        return float(text)

    def _ledger_for_fund(self, conn, fund) -> tuple[float, float, float] | None:
        from ledger.trade_ledger import build_fund_ledger_with_db
        from ledger.quotes import marks_for_fund

        marks = marks_for_fund(conn, fund, date.today(), live=False)
        result = build_fund_ledger_with_db(conn, fund, marks, date.today(), strict=False)
        return result.cash_balance, result.positions_value, result.nav

    def refresh_fund_balances(self) -> None:
        from ledger.db import connect_db
        from ledger.trades_db import fetch_fund_manual_balances, init_trade_schema, list_funds

        self._clear_tree(self.balance_tree)
        funds: list = []
        manual: dict = {}
        conn = connect_db(trades_cli._db_path())
        try:
            init_trade_schema(conn)
            try:
                from ledger.fund_cash_reconcile import sync_opening_cash_from_fund_list

                sync_opening_cash_from_fund_list(conn)
            except Exception:
                pass
            funds = list_funds(conn)
            manual = fetch_fund_manual_balances(conn)
            ledger_cache: dict[int, tuple[float, float, float] | None] = {}
            for fund in funds:
                try:
                    ledger_cache[fund.id] = self._ledger_for_fund(conn, fund)
                except Exception:
                    ledger_cache[fund.id] = None

            for fund in funds:
                m = manual.get(fund.id)
                led = ledger_cache.get(fund.id)
                led_cash = led[0] if led else None
                led_nav = led[2] if led else None
                self.balance_tree.insert(
                    "",
                    tk.END,
                    iid=str(fund.id),
                    values=(
                        fund.name,
                        fund.owner,
                        fund.currency,
                        _fmt_fund_money(m.nav if m else None, fund.currency),
                        _fmt_fund_money(m.cash_balance if m else None, fund.currency),
                        _fmt_fund_money(m.positions_value if m else None, fund.currency),
                        m.balance_date.isoformat() if m else "—",
                        _fmt_fund_money(led_nav, fund.currency),
                        _fmt_fund_money(led_cash, fund.currency),
                    ),
                )
        finally:
            conn.close()

        self.bal_summary_var.set(
            f"{len(funds)} fund(s) · {len(manual)} manual balance(s) on file · "
            "ledger totals from trades + stored mark prices"
        )
        if self.bal_fund_var.get():
            self._load_balance_form_for_fund()
        self.status_var.set("Fund balances updated.")

    def _on_balance_row_selected(self, _event=None) -> None:
        sel = self.balance_tree.selection()
        if not sel:
            return
        fund_id = int(sel[0])
        fund = next((f for f in self._funds if f.id == fund_id), None)
        if fund:
            self.bal_fund_var.set(fund.name)
            self._load_balance_form_for_fund()

    def _load_balance_form_for_fund(self, _event=None) -> None:
        from ledger.db import connect_db
        from ledger.trades_db import fetch_fund_manual_balances, get_fund_by_name, init_trade_schema

        name = self.bal_fund_var.get().strip()
        if not name:
            return
        conn = connect_db(trades_cli._db_path())
        try:
            init_trade_schema(conn)
            fund = get_fund_by_name(conn, name)
            if not fund:
                return
            manual = fetch_fund_manual_balances(conn).get(fund.id)
        finally:
            conn.close()

        if manual:
            self.bal_date_var.set(manual.balance_date.isoformat())
            self.bal_nav_var.set(f"{manual.nav:g}")
            self.bal_cash_var.set("" if manual.cash_balance is None else f"{manual.cash_balance:g}")
            self.bal_pos_var.set(
                "" if manual.positions_value is None else f"{manual.positions_value:g}"
            )
            self.bal_notes_var.set(manual.notes or "")
        else:
            self.bal_date_var.set(date.today().isoformat())
            self.bal_nav_var.set("")
            self.bal_cash_var.set("")
            self.bal_pos_var.set("")
            self.bal_notes_var.set("")

    def save_manual_fund_balance(self) -> None:
        from ledger.db import connect_db
        from ledger.trades_db import get_fund_by_name, init_trade_schema, upsert_fund_manual_balance

        name = self.bal_fund_var.get().strip()
        if not name:
            messagebox.showwarning("Fund balance", "Select a fund.")
            return
        try:
            as_of = _parse_trade_date(self.bal_date_var.get())
            nav = self._parse_optional_amount(self.bal_nav_var.get())
            if nav is None:
                raise ValueError("Enter a total balance (NAV).")
            cash = self._parse_optional_amount(self.bal_cash_var.get())
            pos = self._parse_optional_amount(self.bal_pos_var.get())
            notes = self.bal_notes_var.get().strip() or None
        except ValueError as e:
            messagebox.showerror("Fund balance", str(e))
            return

        conn = connect_db(trades_cli._db_path())
        try:
            init_trade_schema(conn)
            fund = get_fund_by_name(conn, name)
            if not fund:
                messagebox.showerror("Fund balance", f"Unknown fund {name!r}.")
                return
            upsert_fund_manual_balance(
                conn,
                fund.id,
                as_of,
                nav,
                cash_balance=cash,
                positions_value=pos,
                notes=notes,
            )
        finally:
            conn.close()

        self.status_var.set(f"Saved manual balance for {name}.")
        self.refresh_fund_balances()

    def clear_manual_fund_balance(self) -> None:
        from ledger.db import connect_db
        from ledger.trades_db import clear_fund_manual_balance, get_fund_by_name, init_trade_schema

        name = self.bal_fund_var.get().strip()
        if not name:
            messagebox.showwarning("Fund balance", "Select a fund.")
            return
        conn = connect_db(trades_cli._db_path())
        try:
            init_trade_schema(conn)
            fund = get_fund_by_name(conn, name)
            if not fund:
                return
            removed = clear_fund_manual_balance(conn, fund.id)
        finally:
            conn.close()

        if removed:
            self.bal_nav_var.set("")
            self.bal_cash_var.set("")
            self.bal_pos_var.set("")
            self.bal_notes_var.set("")
            self.status_var.set(f"Cleared manual balance for {name}.")
            self.refresh_fund_balances()
        else:
            messagebox.showinfo("Fund balance", f"No manual balance stored for {name}.")

    def _clear_tree(self, tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def refresh_trade_list(self) -> None:
        fund_name = self.trade_fund_var.get().strip()
        self._clear_tree(self.trade_tree)
        if not fund_name:
            return
        from ledger.db import connect_db
        from ledger.trades_db import fetch_trades_for_fund, get_fund_by_name, init_trade_schema

        conn = connect_db(trades_cli._db_path())
        try:
            init_trade_schema(conn)
            fund = get_fund_by_name(conn, fund_name)
            if not fund:
                return
            trades = fetch_trades_for_fund(conn, fund.id)
        finally:
            conn.close()

        for t in reversed(trades):
            from ledger.option_symbols import display_option_label

            label = t.ticker
            if t.is_option:
                label = display_option_label(occ_symbol=t.ticker)
            self.trade_tree.insert(
                "",
                tk.END,
                values=(
                    t.id,
                    t.trade_date.isoformat(),
                    t.instrument_type,
                    t.side.upper(),
                    label,
                    f"{t.shares:g}",
                    f"{t.price:.2f}",
                    f"{t.fees:.2f}",
                ),
            )

    def refresh_portfolio_view(self, *, use_live: bool = False) -> None:
        self._portfolio_job += 1
        job = self._portfolio_job
        owner = self.port_owner_var.get().strip()
        fund_filter = self._port_fund_filter()
        market_sel = self.port_market_var.get().strip()
        label = _port_owner_label(owner)

        if use_live:
            self.status_var.set("Fetching live prices…")
        else:
            self.status_var.set("Loading portfolio…")
        self.update_idletasks()

        import threading

        def worker() -> None:
            err: str | None = None
            cards = []
            summary = ""
            try:
                from reports.portfolio_data import load_portfolio_holdings

                cards, summary = load_portfolio_holdings(
                    trades_cli._db_path(),
                    owner=owner,
                    fund_filter=fund_filter,
                    market_sel=market_sel,
                    use_live=use_live,
                    owner_label=label,
                    include_sheet_compare=use_live,
                )
                self._holdings_loaded = True
            except Exception as e:
                err = str(e)
            self.after(
                0,
                lambda: self._apply_portfolio_view(
                    job, cards, summary, err, label, owner, use_live
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_portfolio_view(
        self,
        job: int,
        cards,
        summary: str,
        error: str | None,
        label: str,
        owner: str,
        use_live: bool,
    ) -> None:
        if job != self._portfolio_job:
            return
        if error:
            self.portfolio_cards.clear()
            self.port_summary_var.set(f"Ledger holdings: unavailable ({error})")
            self.status_var.set(f"Portfolio error: {error}")
            self._portfolio_live_on_show = False
            return
        self.portfolio_cards.set_holdings(cards)
        self.port_summary_var.set(summary)
        if use_live:
            self.status_var.set(f"Live prices updated · {datetime.now():%H:%M}")
        else:
            self.status_var.set(f"Portfolio view updated for {label}.")
        if not use_live and cards:
            self._refresh_portfolio_sheet_flags(job, owner, cards)
        if not use_live and self._portfolio_live_on_show:
            self._portfolio_live_on_show = False
            self.refresh_portfolio_view(use_live=True)

    def _refresh_portfolio_sheet_flags(self, job: int, owner: str, cards) -> None:
        """Background sheet compare for amber discrepancy rows (non-blocking)."""
        self._portfolio_sheet_job += 1
        sheet_job = self._portfolio_sheet_job
        import threading

        def worker() -> None:
            try:
                from reports.portfolio_data import apply_sheet_discrepancies, sheet_shares_for_owner

                sheet_shares = sheet_shares_for_owner(owner)
                updated = apply_sheet_discrepancies(cards, sheet_shares)
            except Exception:
                return
            self.after(
                0,
                lambda: self._apply_portfolio_sheet_flags(job, sheet_job, updated),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_portfolio_sheet_flags(self, portfolio_job: int, sheet_job: int, cards) -> None:
        if portfolio_job != self._portfolio_job or sheet_job != self._portfolio_sheet_job:
            return
        self.portfolio_cards.set_holdings(cards)

    def refresh_ledger_detail_view(self, *, use_live: bool = False) -> None:
        owner = self.port_owner_var.get().strip()
        fund_filter = self._port_fund_filter()
        label = _port_owner_label(owner)

        self._clear_tree(self.book_tree)
        self._clear_tree(self.disc_tree)

        summary_parts: list[str] = []

        try:
            from ledger.db import connect_db
            from reports.holdings_compare import collect_ledger_card_positions
            from ledger.trade_ledger import build_fund_ledger_with_db
            from ledger.quotes import live_quotes_enabled, marks_for_fund
            from ledger.trades_db import get_fund_by_name, init_trade_schema, list_funds

            conn = connect_db(trades_cli._db_path())
            try:
                init_trade_schema(conn)
                try:
                    from ledger.fund_cash_reconcile import sync_opening_cash_from_fund_list

                    sync_opening_cash_from_fund_list(conn)
                except Exception:
                    pass
                live_fund = " · live marks" if use_live and live_quotes_enabled(force=True) else ""

                if fund_filter:
                    fund = get_fund_by_name(conn, fund_filter)
                    if fund:
                        marks = marks_for_fund(
                            conn,
                            fund,
                            date.today(),
                            live=use_live,
                            force_live=use_live,
                        )
                        result = build_fund_ledger_with_db(
                            conn, fund, marks, date.today(), strict=False
                        )
                        cur = fund.currency
                        for p in sorted(result.positions, key=lambda x: -(x.pct_of_nav or 0)):
                            self.book_tree.insert(
                                "",
                                tk.END,
                                tags=(_pl_row_tag(p.unrealized_pl),),
                                values=(
                                    p.ticker,
                                    f"{p.shares:g}",
                                    _fmt_money(p.avg_cost, cur),
                                    _fmt_money(p.mark_price, cur) if p.mark_price is not None else "—",
                                    _fmt_money(p.market_value, cur) if p.market_value is not None else "—",
                                    _fmt_money(p.unrealized_pl, cur) if p.unrealized_pl is not None else "—",
                                    _fmt_money(p.cumulative_realized_pl, cur),
                                ),
                            )
                        summary_parts.append(
                            f"Fund “{fund_filter}”: cash {_fmt_money(result.cash_balance, cur)} · "
                            f"NAV {_fmt_money(result.nav, cur)} · "
                            f"ROI {_fmt_pct(result.fund_roi_pct) if result.fund_roi_pct is not None else '—'}"
                            f"{live_fund}"
                        )
                else:
                    positions = collect_ledger_card_positions(
                        conn,
                        owner=owner,
                        fund_name=None,
                        live=use_live,
                        force_live=use_live,
                    )
                    for pos in positions:
                        cur = _currency_loc(pos.currency)
                        self.book_tree.insert(
                            "",
                            tk.END,
                            tags=(_pl_row_tag(pos.unrealized_pl),),
                            values=(
                                pos.ticker,
                                f"{pos.shares:g}",
                                _fmt_money(pos.avg_cost, cur),
                                _fmt_money(pos.mark_price, cur) if pos.mark_price is not None else "—",
                                _fmt_money(pos.market_value, cur) if pos.market_value is not None else "—",
                                _fmt_money(pos.unrealized_pl, cur) if pos.unrealized_pl is not None else "—",
                                "—",
                            ),
                        )
                    if owner.strip().lower() == "all":
                        owner_funds = list_funds(conn)
                    else:
                        owner_funds = [f for f in list_funds(conn) if f.owner == owner]
                    for fund in owner_funds:
                        marks = marks_for_fund(
                            conn,
                            fund,
                            date.today(),
                            live=use_live,
                            force_live=use_live,
                        )
                        result = build_fund_ledger_with_db(
                            conn, fund, marks, date.today(), strict=False
                        )
                        cur = fund.currency
                        summary_parts.append(
                            f"Fund “{fund.name}”: cash {_fmt_money(result.cash_balance, cur)} · "
                            f"NAV {_fmt_money(result.nav, cur)} · "
                            f"ROI {_fmt_pct(result.fund_roi_pct) if result.fund_roi_pct is not None else '—'}"
                            f"{live_fund}"
                        )
                    if not owner_funds:
                        summary_parts.append(f"No funds for {label}.")
            finally:
                conn.close()
        except Exception as e:
            summary_parts.append(f"Trade ledger: unavailable ({e})")

        try:
            from reports.holdings_compare import compare_ledger_to_sheet, format_discrepancy_summary

            owner_disc = compare_ledger_to_sheet(trades_cli._db_path(), owner=owner)
            summary_parts.append(format_discrepancy_summary(owner_disc))
            disc_rows = list(owner_disc)
            if fund_filter:
                disc_rows.extend(
                    compare_ledger_to_sheet(
                        trades_cli._db_path(),
                        owner=owner,
                        fund_name=fund_filter,
                    )
                )
            shown: set[tuple[str, str]] = set()
            for d in disc_rows:
                key = (d.ticker, d.scope)
                if key in shown:
                    continue
                shown.add(key)
                self.disc_tree.insert(
                    "",
                    tk.END,
                    tags=("disc",),
                    values=(
                        d.ticker,
                        f"{d.ledger_shares:g}",
                        f"{d.sheet_shares:g}",
                        f"{d.delta:+.g}",
                        d.scope,
                    ),
                )
        except Exception as e:
            summary_parts.append(f"Sheet discrepancy check: unavailable ({e})")

        self.ledger_summary_var.set("  |  ".join(summary_parts))
        self.status_var.set(f"Ledger detail updated for {label}.")

    def refresh_fund_holdings_view(self, *, use_live: bool = False) -> None:
        self._fund_holdings_job = getattr(self, "_fund_holdings_job", 0) + 1
        job = self._fund_holdings_job
        owner = self.fh_owner_var.get().strip()
        label = _port_owner_label(owner)

        if use_live:
            self.status_var.set("Fetching live prices…")
        else:
            self.status_var.set("Loading holdings by fund…")
        self.update_idletasks()

        import threading

        def worker() -> None:
            rows = []
            err: str | None = None
            try:
                from ledger.db import connect_db
                from reports.holdings_compare import collect_fund_holdings_rows
                from ledger.trades_db import init_trade_schema

                conn = connect_db(trades_cli._db_path())
                try:
                    init_trade_schema(conn)
                    rows = collect_fund_holdings_rows(
                        conn, owner=owner, live=use_live, force_live=use_live
                    )
                finally:
                    conn.close()
            except Exception as e:
                err = str(e)
            self.after(0, lambda: self._apply_fund_holdings_view(job, rows, err, label, use_live))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_fund_holdings_view(
        self, job: int, rows, error: str | None, label: str, use_live: bool
    ) -> None:
        if job != getattr(self, "_fund_holdings_job", None):
            return
        if error:
            self._fund_holdings_rows = []
            self._clear_tree(self.fund_holdings_tree)
            self.fh_summary_var.set(f"Holdings by fund: unavailable ({error})")
            self.status_var.set(f"Holdings by fund error: {error}")
            return
        self._fund_holdings_rows = rows
        self._render_fund_holdings_rows()
        n_funds = len({(r.owner, r.fund) for r in rows})
        self.fh_summary_var.set(
            f"{label}: {len(rows)} holdings across {n_funds} fund(s) · all prices in USD "
            "(Fund Ccy shows the fund's own reporting currency) · "
            "click a column header to sort · same ticker in multiple funds shows as separate rows."
        )
        if use_live:
            self.status_var.set(f"Live prices updated · {datetime.now():%H:%M}")
        else:
            self.status_var.set(f"Holdings by fund updated for {label}.")

    def _render_fund_holdings_rows(self) -> None:
        from reports.portfolio_data import _matches_port_market

        market_sel = self.fh_market_var.get().strip()
        ticker_sel = self.fh_ticker_var.get().strip().upper()

        rows = [
            r
            for r in getattr(self, "_fund_holdings_rows", [])
            if _matches_port_market(r.market, market_sel)
            and (not ticker_sel or ticker_sel in r.ticker)
        ]

        sort_col, sort_desc = getattr(self, "_fund_holdings_sort_state", (None, False))
        if sort_col:
            key_fns = {
                "owner": lambda r: r.owner,
                "fund": lambda r: r.fund,
                "ccy": lambda r: r.native_currency,
                "ticker": lambda r: r.ticker,
                "shares": lambda r: r.shares,
                "avg": lambda r: r.avg_cost,
                "mark": lambda r: r.mark_price or 0.0,
                "value": lambda r: r.market_value or 0.0,
                "unreal": lambda r: r.unrealized_pl or 0.0,
                "pct": lambda r: r.pct_of_fund or 0.0,
            }
            rows = sorted(rows, key=key_fns[sort_col], reverse=sort_desc)

        self._clear_tree(self.fund_holdings_tree)
        for i, r in enumerate(rows):
            self.fund_holdings_tree.insert(
                "",
                tk.END,
                tags=_tree_row_tags(i, _pl_row_tag(r.unrealized_pl)),
                values=(
                    r.owner,
                    r.fund,
                    r.native_currency,
                    r.ticker,
                    f"{r.shares:g}",
                    _fmt_money(r.avg_cost),
                    _fmt_money(r.mark_price) if r.mark_price is not None else "—",
                    _fmt_money(r.market_value) if r.market_value is not None else "—",
                    _fmt_money(r.unrealized_pl) if r.unrealized_pl is not None else "—",
                    _fmt_pct(r.pct_of_fund) if r.pct_of_fund is not None else "—",
                ),
            )

    def _sort_fund_holdings_tree(self, col: str) -> None:
        prev_col, prev_desc = getattr(self, "_fund_holdings_sort_state", (None, False))
        desc = not prev_desc if col == prev_col else False
        self._fund_holdings_sort_state = (col, desc)
        self._render_fund_holdings_rows()

    def create_fund(self) -> None:
        name = self.fund_name_var.get().strip()
        if not name:
            messagebox.showwarning("New fund", "Enter a fund name.")
            return
        try:
            cash = float(self.fund_cash_var.get().strip().replace(",", "") or "0")
        except ValueError:
            messagebox.showerror("New fund", "Opening cash must be a number.")
            return
        try:
            trades_cli.run_fund_add(
                name,
                self.fund_owner_var.get().strip(),
                cash,
                self.fund_currency_var.get().strip(),
            )
        except ValueError as e:
            messagebox.showerror("New fund", str(e))
            return
        self.fund_name_var.set("")
        self.trade_fund_var.set(name)
        self.port_owner_var.set(self.fund_owner_var.get())
        self.port_fund_var.set(name)
        self.refresh_funds()
        messagebox.showinfo("New fund", f"Created fund “{name}”.")
        self.status_var.set(f"Created fund {name}.")

    def _sync_instrument_form(self) -> None:
        is_option = self.instrument_var.get() == "option"
        if is_option:
            self.option_frame.pack(fill=tk.X, before=self._trade_row3)
            self.qty_label.configure(text="Contracts")
            self.price_label.configure(text="Premium")
        else:
            self.option_frame.pack_forget()
            self.qty_label.configure(text="Shares")
            self.price_label.configure(text="Price")
        ticker_lbl = self._trade_ticker_label
        if ticker_lbl is not None:
            ticker_lbl.configure(text="Ticker / OCC" if is_option else "Ticker")

    def clear_trade_form(self) -> None:
        self.trade_date_var.set("")
        self.instrument_var.set("stock")
        self.ticker_var.set("")
        self.opt_expiry_var.set("")
        self.opt_strike_var.set("")
        self.opt_put_call_var.set("call")
        self._sync_instrument_form()
        self.side_var.set("buy")
        self.shares_var.set("")
        self.price_var.set("")
        self.fees_var.set("0")
        self.notes_var.set("")

    def save_trade(self) -> None:
        fund_name = self.trade_fund_var.get().strip()
        if not fund_name:
            messagebox.showwarning("Save trade", "Select or create a fund first.")
            return
        instrument = self.instrument_var.get().strip().lower()
        ticker = self.ticker_var.get().strip().upper()
        if not ticker:
            messagebox.showwarning("Save trade", "Enter a ticker.")
            return
        try:
            trade_date = _parse_trade_date(self.trade_date_var.get())
            shares = float(self.shares_var.get().strip().replace(",", ""))
            price = float(self.price_var.get().strip().replace(",", ""))
            fees = float(self.fees_var.get().strip().replace(",", "") or "0")
        except ValueError as e:
            messagebox.showerror("Save trade", str(e) if str(e) else "Check shares, price, and fees are numbers.")
            return

        if shares <= 0 or price < 0 or fees < 0:
            messagebox.showerror("Save trade", "Quantity must be > 0; price and fees must be ≥ 0.")
            return

        expiry_raw = self.opt_expiry_var.get().strip()
        expiry = date.fromisoformat(expiry_raw) if expiry_raw else None
        strike_raw = self.opt_strike_var.get().strip().replace(",", "")
        strike = float(strike_raw) if strike_raw else None
        put_call = self.opt_put_call_var.get().strip().lower() or None

        if instrument == "option":
            from ledger.option_symbols import is_occ_symbol

            if not is_occ_symbol(ticker) and not all([expiry, strike, put_call]):
                messagebox.showwarning(
                    "Save trade",
                    "Enter an OCC symbol in Ticker, or the underlying symbol plus expiry, strike, and call/put.",
                )
                return

        notes = self.notes_var.get().strip() or None
        try:
            trades_cli.run_trade_add(
                fund_name,
                ticker,
                self.side_var.get(),
                shares,
                price,
                trade_date,
                fees,
                notes,
                instrument_type=instrument,
                expiry=expiry,
                strike=strike,
                put_call=put_call,
            )
        except ValueError as e:
            messagebox.showerror("Save trade", str(e))
            return

        used = "today" if not self.trade_date_var.get().strip() else trade_date.isoformat()
        from reports.fund_balance_history import clear_fund_balance_series_cache
        from reports.heatmap_data import clear_heatmap_caches
        from ledger.quotes import clear_historical_close_cache

        clear_fund_balance_series_cache()
        clear_heatmap_caches()
        clear_historical_close_cache()
        qty_word = "contracts" if instrument == "option" else "shares"
        self.status_var.set(
            f"Saved {self.side_var.get()} {shares:g} {qty_word} {ticker} "
            f"@ {price} on {used} → {fund_name}"
        )
        self.refresh_trade_list()
        if self._port_fund_filter() in (None, fund_name):
            self.refresh_portfolio_view()
        self.refresh_ledger_detail_view()
        self.ticker_var.set("")
        self.shares_var.set("")
        self.price_var.set("")
        self.fees_var.set("0")
        self.notes_var.set("")


def _ensure_gui_deps() -> None:
    """Check the heat-map dependency. Install it from requirements.txt, never at runtime."""
    try:
        import squarify  # noqa: F401
    except ImportError as e:
        import sys

        raise RuntimeError(
            "squarify is required for the heat map tab. Install the pinned requirements:\n"
            f"  {sys.executable} -m pip install -r requirements.txt"
        ) from e


def main() -> None:
    _ensure_gui_deps()
    app = TradeEntryApp()
    app.mainloop()


if __name__ == "__main__":
    main()
