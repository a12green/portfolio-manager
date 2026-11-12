"""Display formatting and tag rules for the GUI. Pure functions, no Tk."""

from __future__ import annotations

from datetime import date, datetime


def port_owner_label(owner: str, display: dict[str, str]) -> str:
    if owner.strip().lower() == "all":
        return "All owners"
    return display.get(owner, owner)


def tree_row_tags(row_index: int, extra: str = "") -> tuple[str, ...]:
    tags: list[str] = []
    if row_index % 2 == 1:
        tags.append("stripe")
    if extra:
        tags.append(extra)
    return tuple(tags)


def pl_row_tag(amount: float | None) -> str:
    """Green background for gain/break-even, red for loss."""
    return "avg_good" if (amount or 0) >= 0 else "avg_bad"


def parse_trade_date(raw: str) -> date:
    text = raw.strip()
    if not text:
        return date.today()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Use a date like YYYY-MM-DD (or leave blank for today).")


def fmt_money(amount: float, location: str = "") -> str:
    loc = str(location).upper()
    if loc == "AUS":
        return f"A${amount:,.2f}"
    return f"${amount:,.2f}"


def fmt_pct(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def currency_loc(currency: str) -> str:
    c = str(currency or "").upper()
    if c == "AUD":
        return "AUS"
    if c == "GBP":
        return "UK"
    return "US"


def fmt_fund_money(amount: float | None, currency: str) -> str:
    if amount is None:
        return "—"
    return fmt_money(amount, currency_loc(currency))


def fmt_price_cell(amount: float | None, location: str = "") -> str:
    if amount is None:
        return "—"
    return fmt_money(amount, location)


def holding_market(loc: str, ticker: str = "") -> str:
    """AU for Australian listings; otherwise US (holdings file Location convention)."""
    loc = str(loc or "").strip().upper()
    tick = str(ticker or "").strip().upper()
    if loc in ("AUS", "AUD", "AU") or tick.startswith("ASX:"):
        return "AU"
    return "US"


def matches_port_market(loc: str, ticker: str, market_sel: str) -> bool:
    sel = market_sel.strip()
    if not sel or sel == "All":
        return True
    m = holding_market(loc, ticker)
    if sel.startswith("Austral"):
        return m == "AU"
    if sel.startswith("United") or sel == "US":
        return m == "US"
    return True
