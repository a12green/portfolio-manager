from __future__ import annotations

import html as html_module
from datetime import date

from ledger.trade_ledger import FundLedgerResult, ProcessedTrade


def _fmt_money(amount: float, currency: str) -> str:
    if currency == "AUD":
        return f"A${amount:,.2f}"
    return f"${amount:,.2f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def _fmt_shares(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.4f}"


def build_trade_report(
    result: FundLedgerResult,
    performance_history: list[dict] | None = None,
) -> tuple[str, str]:
    f = result.fund
    cur = f.currency
    title = f"Trade & fund report — {f.name} ({f.owner})"

    plain: list[str] = [
        title,
        f"As of {result.as_of.isoformat()}",
        "",
        "Fund summary",
        f"  Cash balance:        {_fmt_money(result.cash_balance, cur)}",
        f"  Positions (marked):  {_fmt_money(result.positions_value, cur)}",
        f"  NAV:                 {_fmt_money(result.nav, cur)}",
        f"  Invested capital:    {_fmt_money(result.invested_capital, cur)}",
        f"  Realized P/L:        {_fmt_money(result.cumulative_realized_pl, cur)}",
        f"  Fund ROI:            {_fmt_pct(result.fund_roi_pct)}",
        "",
    ]

    html_parts: list[str] = [
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{html_module.escape(title)}</title></head>',
        '<body style="font-family: system-ui, sans-serif; color: #111; max-width: 960px;">',
        f"<h2>{html_module.escape(title)}</h2>",
        f"<p>As of <strong>{result.as_of.isoformat()}</strong></p>",
        '<table border="0" cellpadding="4" style="font-size: 14px; margin-bottom: 1.5em;">',
        f"<tr><td>Cash balance</td><td align=\"right\"><strong>{html_module.escape(_fmt_money(result.cash_balance, cur))}</strong></td></tr>",
        f"<tr><td>Positions (marked)</td><td align=\"right\"><strong>{html_module.escape(_fmt_money(result.positions_value, cur))}</strong></td></tr>",
        f"<tr><td>NAV</td><td align=\"right\"><strong>{html_module.escape(_fmt_money(result.nav, cur))}</strong></td></tr>",
        f"<tr><td>Invested capital</td><td align=\"right\">{html_module.escape(_fmt_money(result.invested_capital, cur))}</td></tr>",
        f"<tr><td>Realized P/L</td><td align=\"right\">{html_module.escape(_fmt_money(result.cumulative_realized_pl, cur))}</td></tr>",
        f"<tr><td>Fund ROI</td><td align=\"right\"><strong>{html_module.escape(_fmt_pct(result.fund_roi_pct))}</strong></td></tr>",
        "</table>",
    ]

    def _table_section(heading: str, headers: list[str], rows: list[list[str]], plain_header: str) -> None:
        plain.append(heading)
        plain.append(plain_header)
        plain.append("-" * len(plain_header))
        for row in rows:
            plain.append("  ".join(f"{c:>14}" for c in row))
        plain.append("")
        html_parts.append(f"<h3>{html_module.escape(heading)}</h3>")
        html_parts.append(
            '<table border="1" cellpadding="6" cellspacing="0" '
            'style="border-collapse: collapse; font-size: 13px; width: 100%; margin-bottom: 1.25em;">'
        )
        html_parts.append('<thead style="background: #f4f4f4;"><tr>')
        for h in headers:
            align = "right" if h not in ("Date", "Ticker", "Side", "Fund") else "left"
            html_parts.append(f'<th align="{align}">{html_module.escape(h)}</th>')
        html_parts.append("</tr></thead><tbody>")
        for row in rows:
            html_parts.append("<tr>")
            for i, cell in enumerate(row):
                align = "right" if headers[i] not in ("Date", "Ticker", "Side", "Fund") else "left"
                html_parts.append(f'<td align="{align}">{html_module.escape(cell)}</td>')
            html_parts.append("</tr>")
        html_parts.append("</tbody></table>")

    trade_rows: list[list[str]] = []
    for pt in result.processed:
        tr = pt.trade
        trade_rows.append(
            [
                tr.trade_date.isoformat(),
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
    if trade_rows:
        _table_section(
            "Trade ledger (cash impact per trade)",
            ["Date", "Ticker", "Side", "Shares", "Price", "Cash impact", "Cash after", "Realized P/L", "Trade ROI"],
            trade_rows,
            "Date Ticker Side Shares Price CashΔ CashAfter Realized ROI",
        )
    else:
        plain.append("No trades recorded yet.\n")
        html_parts.append("<p><em>No trades recorded yet.</em></p>")

    pos_rows: list[list[str]] = []
    for p in sorted(result.positions, key=lambda x: -(x.pct_of_nav or 0)):
        pos_rows.append(
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
    if pos_rows:
        _table_section(
            "Open positions (average cost)",
            [
                "Ticker",
                "Shares",
                "Avg cost",
                "Mark",
                "Mkt value",
                "Unrealized P/L",
                "Unrealized ROI",
                "% of NAV",
                "Realized P/L",
            ],
            pos_rows,
            "Ticker Shares AvgCost Mark Value Unrealized",
        )

    stock_rows: list[list[str]] = []
    for s in sorted(result.stock_summaries, key=lambda x: x.ticker):
        stock_rows.append(
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
    if stock_rows:
        _table_section(
            "Stock performance (average cost & average profit per share)",
            ["Ticker", "Shares", "Avg cost", "Avg profit/share", "Realized P/L", "Unrealized P/L", "Total P/L"],
            stock_rows,
            "Ticker Shares AvgCost AvgProfit Realized Unrealized Total",
        )

    if performance_history:
        hist_rows = [
            [
                str(h["snapshot_date"]),
                _fmt_money(float(h["cash_balance"]), cur),
                _fmt_money(float(h["positions_value"]), cur),
                _fmt_money(float(h["nav"]), cur),
                _fmt_pct(float(h["roi_pct"])) if h["roi_pct"] is not None else "—",
            ]
            for h in performance_history
        ]
        _table_section(
            "Fund performance over time (run --trade-snapshot after trades)",
            ["Date", "Cash", "Positions", "NAV", "ROI %"],
            hist_rows,
            "Date Cash Positions NAV ROI",
        )

    html_parts.append("</body></html>")
    return "\n".join(plain), "".join(html_parts)
