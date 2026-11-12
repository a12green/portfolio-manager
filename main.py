from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
import argparse
import html as html_module
import os
import smtplib
from datetime import date, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path
from typing import Sequence

import pandas as pd
import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

from ledger.option_symbols import is_occ_symbol
from ledger.db import (
    StockSnapshotRow,
    connect_db,
    init_schema,
    replace_fund_weekly_snapshots,
    upsert_stock_daily_snapshots,
)
from importers.funds import load_fund_lines_from_sheet
from importers.import_funds import import_fund_history_csv
import trades_cli
from ledger.money import parse_money_scalar, parse_pct_scalar

_EXPORT_SYMBOL = "Symbol"
_EXPORT_SHARES = "Share Qty"
_EXPORT_AVG = "Average Purchase Price"
_EXPORT_LAST = "Current Price"
_EXPORT_PORTFOLIO_PCT = "Portfolio %"
_EXPORT_LOCATION = "Location"
_STOCK_COL = "Stock"

_COLOR_GOOD = "#2e7d32"
_COLOR_BAD = "#c62828"

# Section order in the email (unknown names from the sheet are appended after these).
_PORTFOLIO_SECTION_ORDER = (OWNER_A, OWNER_C, OWNER_B)
# Labels in the email (the sheet uses the owner name from settings.json).
_PORTFOLIO_DISPLAY = {OWNER_A: OWNER_A, OWNER_C: OWNER_C, OWNER_B: OWNER_B}


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _db_path() -> Path:
    return Path(os.getenv("STORAGE_DB_PATH", "data/portfolio_history.sqlite3")).expanduser()


def _pdf_output_path() -> Path:
    return Path(os.getenv("PDF_OUTPUT_PATH", "reports/portfolio_report.pdf")).expanduser()


def iso_week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def load_sheet_dataframe() -> pd.DataFrame:
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service-account.json")
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    sheet_name = os.getenv("GOOGLE_SHEET_NAME", "Portfolio")

    if not spreadsheet_id:
        raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID is required.")

    sa_path = Path(service_account_file).expanduser()
    if not sa_path.is_file():
        raise FileNotFoundError(
            f"Service account key not found at {sa_path.resolve()}. "
            "Download the JSON key from Google Cloud and save it there, or unset GOOGLE_SHEETS_SPREADSHEET_ID "
            "to use local Stocks.csv / PORTFOLIO_CSV_PATH instead."
        )

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    credentials = Credentials.from_service_account_file(str(sa_path), scopes=scopes)
    client = gspread.authorize(credentials)

    worksheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
    records = worksheet.get_all_records()
    if not records:
        raise ValueError(f"Sheet '{sheet_name}' is empty.")

    return pd.DataFrame(records)


def load_csv_dataframe(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"CSV '{path}' is empty.")
    return df


def load_portfolio_dataframe() -> pd.DataFrame:
    """
    Prefer Google Sheets when configured; otherwise load a local CSV for development.

    Expected columns: export layout Symbol / Share Qty / Average Purchase Price / Current Price
    plus Stock; portfolio blocks are introduced by rows with Stock=Portfolio and Symbol=name
    (see Stocks.csv).
    """
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    csv_override = os.getenv("PORTFOLIO_CSV_PATH", "").strip()

    if spreadsheet_id:
        return load_sheet_dataframe()
    if csv_override:
        return load_csv_dataframe(Path(csv_override).expanduser())
    default_csv = _project_dir() / "Stocks.csv"
    if default_csv.is_file():
        return load_csv_dataframe(default_csv)
    raise ValueError(
        "No data source configured: set GOOGLE_SHEETS_SPREADSHEET_ID for Sheets, "
        "or PORTFOLIO_CSV_PATH, or add Stocks.csv next to main.py."
    )


def _parse_export_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Parse combined export: first holdings block is OWNER_A; later blocks follow Stock=Portfolio markers."""
    need = {_EXPORT_SYMBOL, _EXPORT_SHARES, _EXPORT_AVG, _EXPORT_LAST, _STOCK_COL}
    if not need.issubset(set(df.columns)):
        raise ValueError(
            f"Expected export columns {sorted(need)}; got {sorted(df.columns)[:25]}"
            f"{'…' if len(df.columns) > 25 else ''}."
        )

    rows: list[dict[str, object]] = []
    portfolio = OWNER_A
    first_col = df.columns[0]

    for _, row in df.iterrows():
        row0 = str(row.get(first_col, "") or "").strip()
        stock = str(row.get(_STOCK_COL, "") or "").strip()
        sym = str(row.get(_EXPORT_SYMBOL, "") or "").strip()

        # Rows like: <first_col>=Portfolio, Stock=<owner name> (see Stocks.csv)
        if row0 == "Portfolio" and stock:
            portfolio = stock
            continue

        if stock.upper() == "STOCK" and sym.upper() == "SYMBOL":
            continue

        if not sym or sym.upper() == "SYMBOL":
            continue

        shares = pd.to_numeric(row.get(_EXPORT_SHARES), errors="coerce")
        if shares is None or pd.isna(shares) or float(shares) <= 0:
            continue

        avg = parse_money_scalar(row.get(_EXPORT_AVG))
        last = parse_money_scalar(row.get(_EXPORT_LAST))
        if pd.isna(avg) or pd.isna(last):
            continue

        location = ""
        if _EXPORT_LOCATION in df.columns:
            location = str(row.get(_EXPORT_LOCATION, "") or "").strip().upper()

        portfolio_pct = float("nan")
        if _EXPORT_PORTFOLIO_PCT in df.columns:
            portfolio_pct = parse_pct_scalar(row.get(_EXPORT_PORTFOLIO_PCT))

        rows.append(
            {
                "Portfolio": portfolio,
                "Ticker": sym,
                "Shares": float(shares),
                "AvgCost": float(avg),
                "LastPrice": float(last),
                "Location": location,
                "portfolio_pct": portfolio_pct,
            }
        )

    if not rows:
        raise ValueError("No valid holding rows found in export.")

    return pd.DataFrame(rows)


def _portfolio_sort_key(series: pd.Series) -> pd.Series:
    order = {name: i for i, name in enumerate(_PORTFOLIO_SECTION_ORDER)}
    return series.map(lambda p: order.get(str(p), len(_PORTFOLIO_SECTION_ORDER)))


def _prepare_holdings(df: pd.DataFrame) -> pd.DataFrame:
    work = _parse_export_dataframe(df)
    work["Portfolio"] = work["Portfolio"].astype(str).str.strip()
    work["Ticker"] = work["Ticker"].astype(str).str.strip()
    work["Shares"] = pd.to_numeric(work["Shares"], errors="coerce")
    work["AvgCost"] = pd.to_numeric(work["AvgCost"], errors="coerce")
    work["LastPrice"] = pd.to_numeric(work["LastPrice"], errors="coerce")
    work = work.dropna(subset=["Ticker", "Shares", "AvgCost", "LastPrice", "Portfolio"])
    work = work[(work["Ticker"] != "") & (work["Portfolio"] != "")]
    if work.empty:
        raise ValueError("No valid rows after parsing holdings.")

    work["cost_basis"] = work["Shares"] * work["AvgCost"]
    work["mkt_value"] = work["Shares"] * work["LastPrice"]
    work["pl_dollar"] = work["mkt_value"] - work["cost_basis"]
    work["pl_pct"] = (work["pl_dollar"] / work["cost_basis"]) * 100.0
    work = work[work["cost_basis"] > 0]
    if work.empty:
        raise ValueError("No valid rows after removing zero cost basis positions.")

    if "Location" not in work.columns:
        work["Location"] = ""
    work["Location"] = work["Location"].astype(str).str.strip().str.upper()
    if "portfolio_pct" not in work.columns:
        work["portfolio_pct"] = float("nan")
    work["portfolio_pct"] = pd.to_numeric(work["portfolio_pct"], errors="coerce")

    for pname in work["Portfolio"].unique():
        mask = work["Portfolio"] == pname
        section_mkt = float(work.loc[mask, "mkt_value"].sum())
        if section_mkt <= 0:
            continue
        computed = work.loc[mask, "mkt_value"] / section_mkt * 100.0
        sheet_pct = work.loc[mask, "portfolio_pct"]
        work.loc[mask, "portfolio_pct"] = sheet_pct.where(sheet_pct.notna(), computed)

    work["portfolio_pct"] = work["portfolio_pct"].fillna(0.0)

    return work.sort_values(
        ["Portfolio", "portfolio_pct"],
        ascending=[True, False],
        key=lambda s: _portfolio_sort_key(s) if s.name == "Portfolio" else s,
    )


def _row_currency(row: pd.Series) -> str:
    loc = str(row.get("Location", "") or "").strip().upper()
    ticker = str(row.get("Ticker", "") or "").strip().upper()
    if loc == "AUS" or ticker.startswith("ASX:"):
        return "AUD"
    return "USD"


def _fmt_money(x: float, currency: str = "USD") -> str:
    if currency == "AUD":
        return f"A${x:,.2f}"
    return f"${x:,.2f}"


def _fmt_money_plain(x: float, currency: str = "USD") -> str:
    """Plain-text money without thousands separators (keeps column alignment tighter)."""
    if currency == "AUD":
        return f"A${x:.2f}"
    return f"${x:.2f}"


def _fmt_shares(x: float) -> str:
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.2f}"


def _fmt_pct_signed(x: float) -> str:
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.2f}%"


def _portfolio_heading(name: str) -> str:
    return _PORTFOLIO_DISPLAY.get(str(name), str(name))


def _dup_tickers(work: pd.DataFrame) -> set[str]:
    vc = work["Ticker"].value_counts()
    return set(vc[vc > 1].index.astype(str))


def _holding_label(row: pd.Series, dup: set[str]) -> str:
    t = str(row["Ticker"])
    if t in dup:
        return f"{t} ({_portfolio_heading(row['Portfolio'])})"
    return t


def _positions_sorted(sub: pd.DataFrame) -> pd.DataFrame:
    return sub.sort_values("portfolio_pct", ascending=False)


def _avg_cost_color(avg: float, last: float) -> str:
    if avg < last:
        return _COLOR_GOOD
    if avg > last:
        return _COLOR_BAD
    return "#333333"


def _plain_position_block(sub: pd.DataFrame) -> list[str]:
    lines: list[str] = [
        "Ticker       % Port  Shares  Avg cost      Last         Cost basis    Mkt value     P/L         P/L %",
    ]
    for _, r in _positions_sorted(sub).iterrows():
        cur = _row_currency(r)
        avg_note = "v" if float(r["AvgCost"]) < float(r["LastPrice"]) else "^" if float(r["AvgCost"]) > float(r["LastPrice"]) else " "
        lines.append(
            f"{str(r['Ticker']):<12}"
            f"{r['portfolio_pct']:>6.2f}%  "
            f"{_fmt_shares(r['Shares']):>6}  "
            f"{avg_note}{_fmt_money_plain(r['AvgCost'], cur):>12}  "
            f"{_fmt_money_plain(r['LastPrice'], cur):>12}  "
            f"{_fmt_money_plain(r['cost_basis'], cur):>12}  "
            f"{_fmt_money_plain(r['mkt_value'], cur):>12}  "
            f"{_fmt_money_plain(r['pl_dollar'], cur):>12}  "
            f"{_fmt_pct_signed(r['pl_pct']):>8}"
        )
    lines.append("  (v = avg below last, ^ = avg above last)")
    return lines


def _html_money_cell(amount: float, currency: str) -> str:
    return html_module.escape(_fmt_money(amount, currency))


def _html_avg_cost_cell(avg: float, last: float, currency: str) -> str:
    color = _avg_cost_color(avg, last)
    return (
        f'<td align="right" style="color: {color}; font-weight: 600;">'
        f"{html_module.escape(_fmt_money(avg, currency))}</td>"
    )


def _html_position_rows(sub: pd.DataFrame) -> str:
    parts: list[str] = []
    for _, r in _positions_sorted(sub).iterrows():
        cur = _row_currency(r)
        parts.append(
            "<tr>"
            f"<td>{html_module.escape(str(r['Ticker']))}</td>"
            f'<td align="right">{r["portfolio_pct"]:.2f}%</td>'
            f'<td align="right">{html_module.escape(_fmt_shares(r["Shares"]))}</td>'
            f"{_html_avg_cost_cell(float(r['AvgCost']), float(r['LastPrice']), cur)}"
            f'<td align="right">{_html_money_cell(float(r["LastPrice"]), cur)}</td>'
            f'<td align="right">{_html_money_cell(float(r["cost_basis"]), cur)}</td>'
            f'<td align="right">{_html_money_cell(float(r["mkt_value"]), cur)}</td>'
            f'<td align="right">{_html_money_cell(float(r["pl_dollar"]), cur)}</td>'
            f'<td align="right">{html_module.escape(_fmt_pct_signed(r["pl_pct"]))}</td>'
            "</tr>"
        )
    return "".join(parts)


def _currencies_in_frame(sub: pd.DataFrame) -> list[str]:
    return sorted({_row_currency(r) for _, r in sub.iterrows()})


def _subset_currency(sub: pd.DataFrame, currency: str) -> pd.DataFrame:
    return sub[sub.apply(lambda row: _row_currency(row) == currency, axis=1)]


def _totals_lines(title: str, cost: float, mkt: float, currency: str = "USD") -> list[str]:
    pl = mkt - cost
    pct = (pl / cost) * 100.0 if cost else 0.0
    return [
        title,
        "-" * max(8, len(title)),
        f"Cost basis:   {_fmt_money(cost, currency)}",
        f"Market value: {_fmt_money(mkt, currency)}",
        f"P/L:          {_fmt_money(pl, currency)} ({_fmt_pct_signed(pct)})",
        "",
    ]


def _totals_html(cost: float, mkt: float, currency: str = "USD") -> str:
    pl = mkt - cost
    pct = (pl / cost) * 100.0 if cost else 0.0
    return (
        "<table border=\"0\" cellpadding=\"4\" cellspacing=\"0\" style=\"font-size: 14px;\">"
        f"<tr><td>Cost basis:</td><td align=\"right\"><strong>{html_module.escape(_fmt_money(cost, currency))}</strong></td></tr>"
        f"<tr><td>Market value:</td><td align=\"right\"><strong>{html_module.escape(_fmt_money(mkt, currency))}</strong></td></tr>"
        f"<tr><td>P/L:</td><td align=\"right\"><strong>{html_module.escape(_fmt_money(pl, currency))} "
        f"({html_module.escape(_fmt_pct_signed(pct))})</strong></td></tr>"
        "</table>"
    )


def build_holdings_report(work: pd.DataFrame, as_of: date) -> tuple[str, str]:
    dup = _dup_tickers(work)

    gainers = work[work["pl_pct"] > 0].sort_values("pl_pct", ascending=False)
    losers = work[work["pl_pct"] < 0].sort_values("pl_pct", ascending=True)
    g_names = ", ".join(_holding_label(r, dup) for _, r in gainers.iterrows()) or "—"
    l_names = ", ".join(_holding_label(r, dup) for _, r in losers.iterrows()) or "—"

    seen = set(work["Portfolio"].unique())
    ordered_portfolios = [p for p in _PORTFOLIO_SECTION_ORDER if p in seen]
    ordered_portfolios.extend(sorted(p for p in seen if p not in ordered_portfolios))

    as_of_s = as_of.isoformat()
    title = "Portfolio holdings report"

    plain_lines = [
        title,
        "",
        f"As of run date: {as_of_s}",
        "",
    ]

    html_parts: list[str] = [
        f'<!DOCTYPE html>\n<html>\n<head><meta charset="utf-8"><title>{html_module.escape(title)}</title></head>\n'
        f'<body style="font-family: system-ui, sans-serif; color: #111;">\n'
        f'  <h2 style="margin-bottom: 0.25em;">{html_module.escape(title)}</h2>\n'
        f'  <p style="margin-top: 0;">As of run date: <strong>{html_module.escape(as_of_s)}</strong></p>\n'
    ]

    for pname in ordered_portfolios:
        sub = work[work["Portfolio"] == pname]
        if sub.empty:
            continue

        label = _portfolio_heading(pname)
        plain_lines.append(f"Portfolio: {label}")
        plain_lines.append("=" * max(20, len(label) + 12))
        plain_lines.append("")
        plain_lines.append("Positions")
        plain_lines.append("-------")
        plain_lines.extend(_plain_position_block(sub))
        plain_lines.append("")
        for cur in _currencies_in_frame(sub):
            part = _subset_currency(sub, cur)
            plain_lines.extend(
                _totals_lines(
                    f"{label} — subtotal ({cur})",
                    float(part["cost_basis"].sum()),
                    float(part["mkt_value"].sum()),
                    cur,
                )
            )

        html_parts.append(
            f'  <h3 style="margin-top: 1.25em; margin-bottom: 0.35em;">Portfolio: {html_module.escape(_portfolio_heading(pname))}</h3>\n'
            '  <p style="font-size: 12px; color: #555; margin: 0 0 0.5em 0;">'
            "Sorted by % of portfolio (largest first). "
            f'<span style="color: {_COLOR_GOOD};">Green avg cost</span> = below last price; '
            f'<span style="color: {_COLOR_BAD};">red</span> = above last price.</p>\n'
            '  <h4 style="margin: 0.35em 0 0.35em 0; font-size: 15px;">Positions</h4>\n'
            '  <table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; font-size: 14px;">\n'
            '    <thead style="background: #f4f4f4;">\n'
            "      <tr>\n"
            '        <th align="left">Ticker</th>\n'
            '        <th align="right">% Port.</th>\n'
            '        <th align="right">Shares</th>\n'
            '        <th align="right">Avg cost</th>\n'
            '        <th align="right">Last</th>\n'
            '        <th align="right">Cost basis</th>\n'
            '        <th align="right">Mkt value</th>\n'
            '        <th align="right">P/L</th>\n'
            '        <th align="right">P/L %</th>\n'
            "      </tr>\n"
            "    </thead>\n"
            "    <tbody>\n"
            f"      {_html_position_rows(sub)}\n"
            "    </tbody>\n"
            "  </table>\n"
        )
        for cur in _currencies_in_frame(sub):
            part = _subset_currency(sub, cur)
            html_parts.append(
                f'  <h4 style="margin-top: 0.9em;">{html_module.escape(_portfolio_heading(pname))} — subtotal ({cur})</h4>\n'
                f"  {_totals_html(float(part['cost_basis'].sum()), float(part['mkt_value'].sum()), cur)}\n"
            )

    plain_lines.append("All portfolios — combined")
    plain_lines.append("========================")
    plain_lines.append("")
    for cur in _currencies_in_frame(work):
        part = _subset_currency(work, cur)
        plain_lines.extend(
            _totals_lines(
                f"Combined totals ({cur})",
                float(part["cost_basis"].sum()),
                float(part["mkt_value"].sum()),
                cur,
            )
        )
    plain_lines.append(f"Largest gainers (by %): {g_names}")
    plain_lines.append(f"Largest decliners (by %): {l_names}")

    html_parts.append('  <h3 style="margin-top: 1.5em;">All portfolios — combined</h3>\n')
    for cur in _currencies_in_frame(work):
        part = _subset_currency(work, cur)
        html_parts.append(
            f"  <h4 style=\"margin-top: 0.5em;\">Combined totals ({cur})</h4>\n"
            f"  {_totals_html(float(part['cost_basis'].sum()), float(part['mkt_value'].sum()), cur)}\n"
        )
    html_parts.append(
        f'  <p style="margin-top: 1.25em; font-size: 14px;">Largest gainers (by %): <strong>{html_module.escape(g_names)}</strong></p>\n'
        f'  <p style="font-size: 14px;">Largest decliners (by %): <strong>{html_module.escape(l_names)}</strong></p>\n'
        "</body>\n</html>\n"
    )

    plain = "\n".join(plain_lines).rstrip() + "\n"
    html = "".join(html_parts)
    return plain, html


def send_email(
    plain_body: str,
    html_body: str,
    attachments: Sequence[Path] | None = None,
    *,
    to: str | None = None,
    subject: str | None = None,
) -> None:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = _parse_int_env("SMTP_PORT", 587)
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_from = os.getenv("EMAIL_FROM", smtp_user or "")
    email_to = to or os.getenv("EMAIL_TO")
    email_subject = subject or os.getenv("EMAIL_SUBJECT", "Portfolio Progress Update")

    if not smtp_user or not smtp_password:
        raise ValueError("SMTP_USERNAME and SMTP_PASSWORD are required.")
    if not email_to:
        raise ValueError("EMAIL_TO is required.")

    att = list(attachments) if attachments else []

    if att:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = email_subject
        msg["From"] = email_from
        msg["To"] = email_to
        msg["Date"] = formatdate(localtime=True)

        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(plain_body, "plain", "utf-8"))
        alt.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(alt)

        for p in att:
            path = Path(p).expanduser()
            with path.open("rb") as f:
                part = MIMEApplication(f.read(), _subtype="pdf")
            part.add_header("Content-Disposition", "attachment", filename=path.name)
            msg.attach(part)
    else:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = email_subject
        msg["From"] = email_from
        msg["To"] = email_to
        msg["Date"] = formatdate(localtime=True)
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    use_ssl = smtp_port == 465 or os.getenv("SMTP_SSL", "").strip().lower() in ("1", "true", "yes")
    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(email_from, [email_to], msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(email_from, [email_to], msg.as_string())


def run_snapshot_stocks_daily(as_of: date | None = None) -> int:
    as_of = as_of or date.today()
    df = load_portfolio_dataframe()
    work = _prepare_holdings(df)

    rows: list[StockSnapshotRow] = []
    for _, r in work.iterrows():
        rows.append(
            StockSnapshotRow(
                snapshot_date=as_of,
                owner=str(r["Portfolio"]),
                ticker=str(r["Ticker"]),
                shares=float(r["Shares"]),
                avg_cost=float(r["AvgCost"]),
                last_price=float(r["LastPrice"]),
                cost_basis=float(r["cost_basis"]),
                mkt_value=float(r["mkt_value"]),
                pl_dollar=float(r["pl_dollar"]),
                pl_pct=float(r["pl_pct"]),
            )
        )

    dbp = _db_path()
    conn = connect_db(dbp)
    try:
        init_schema(conn)
        n = upsert_stock_daily_snapshots(conn, rows)
    finally:
        conn.close()
    print(f"Saved {n} stock holding row(s) for {as_of.isoformat()} → {dbp.resolve()}")
    return n


def run_snapshot_funds_weekly(week_start: date | None = None) -> None:
    week_start = week_start or iso_week_monday(date.today())
    by_owner = load_fund_lines_from_sheet(week_start)

    dbp = _db_path()
    conn = connect_db(dbp)
    total = 0
    try:
        init_schema(conn)
        for owner in (OWNER_A, OWNER_B, OWNER_C):
            lines = by_owner[owner]
            if lines:
                total += replace_fund_weekly_snapshots(conn, week_start, owner, lines)
    finally:
        conn.close()
    print(
        f"Saved {total} fund line(s) for week starting {week_start.isoformat()} → {dbp.resolve()}"
    )


def run_import_funds_csv(csv_path: Path) -> int:
    dbp = _db_path()
    conn = connect_db(dbp)
    try:
        n = import_fund_history_csv(conn, csv_path)
    finally:
        conn.close()
    print(f"Imported {n} fund weekly row(s) from {csv_path.resolve()} → {dbp.resolve()}")
    return n


def run_pdf_report() -> Path:
    from reports.pdf_report import render_pdf

    dbp = _db_path()
    outp = _pdf_output_path()
    conn = connect_db(dbp)
    try:
        init_schema(conn)
        path = render_pdf(conn, outp, generated_on=date.today())
    finally:
        conn.close()
    print(f"Wrote PDF report → {path.resolve()}")
    return path


def run_email_holdings(attach_pdf_paths: Sequence[Path] | None = None) -> None:
    from ledger.market_calendar import should_send_holdings_email, skip_reason

    if not should_send_holdings_email():
        print(f"Holdings email skipped: {skip_reason()}")
        return
    df = load_portfolio_dataframe()
    work = _prepare_holdings(df)
    plain, html = build_holdings_report(work, date.today())
    send_email(plain, html, attachments=attach_pdf_paths)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Portfolio holdings mailer + SQLite history + PDF reports.")
    parser.add_argument("--snapshot-stocks-daily", action="store_true", help="Upsert today's stock holdings into SQLite.")
    parser.add_argument("--snapshot-funds-weekly", action="store_true", help="Upsert this ISO week's fund totals into SQLite.")
    parser.add_argument("--pdf-report", action="store_true", help="Generate graphical PDF from SQLite history.")
    parser.add_argument("--email-holdings", action="store_true", help="Email holdings report (same content as legacy default).")
    parser.add_argument(
        "--market-status",
        action="store_true",
        help="Print whether US (NYSE) and AU (ASX) markets are open today.",
    )
    parser.add_argument(
        "--import-funds-csv",
        metavar="PATH",
        type=Path,
        help="Backfill fund_weekly_snapshots from a CSV (long or wide format).",
    )

    # Trade & fund ledger
    parser.add_argument("--fund-add", metavar="NAME", help="Create a trade fund with opening cash.")
    parser.add_argument("--fund-owner", default=OWNER_A, help="Owner for --fund-add (one of the owners in settings.json).")
    parser.add_argument("--fund-cash", type=float, default=0.0, help="Opening cash for --fund-add.")
    parser.add_argument("--fund-currency", default="USD", help="Currency for --fund-add (USD or AUD).")
    parser.add_argument("--fund-list", action="store_true", help="List trade funds.")
    parser.add_argument("--trade-add", action="store_true", help="Record a buy/sell trade.")
    parser.add_argument("--trade-fund", help="Fund name for trade commands.")
    parser.add_argument("--trade-ticker", help="Ticker for stocks; OCC symbol or underlying for options.")
    parser.add_argument("--trade-side", choices=["buy", "sell"], help="buy or sell.")
    parser.add_argument("--trade-shares", type=float, help="Share or contract quantity.")
    parser.add_argument("--trade-price", type=float, help="Price per share (premium per share for options).")
    parser.add_argument("--trade-date", type=str, help="Trade date YYYY-MM-DD (default today).")
    parser.add_argument("--trade-fees", type=float, default=0.0, help="Fees for --trade-add.")
    parser.add_argument("--trade-notes", default="", help="Optional note on trade.")
    parser.add_argument(
        "--trade-instrument",
        choices=["stock", "option"],
        default="stock",
        help="Instrument type for --trade-add (default stock).",
    )
    parser.add_argument("--trade-expiry", type=str, help="Option expiry YYYY-MM-DD.")
    parser.add_argument("--trade-strike", type=float, help="Option strike price.")
    parser.add_argument(
        "--trade-put-call",
        choices=["call", "put", "c", "p"],
        help="Option type for --trade-add.",
    )
    parser.add_argument(
        "--trade-multiplier",
        type=float,
        default=None,
        help="Option contract multiplier (default 100).",
    )
    parser.add_argument("--trade-list", action="store_true", help="List trades for --trade-fund.")
    parser.add_argument(
        "--trade-report",
        action="store_true",
        help="Generate a PDF trade/fund report for --trade-fund (see TRADE_REPORT_DIR).",
    )
    parser.add_argument(
        "--trade-report-text",
        action="store_true",
        help="With --trade-report, also print the plain-text report to stdout.",
    )
    parser.add_argument("--email-trade-report", action="store_true", help="Email --trade-report (requires --trade-fund).")
    parser.add_argument("--trade-snapshot", action="store_true", help="Save fund NAV/ROI snapshot for today.")
    parser.add_argument("--import-trades-csv", metavar="PATH", type=Path, help="Import trades from CSV.")
    parser.add_argument(
        "--import-fidelity-csv",
        metavar="PATH",
        type=Path,
        help="Import Fidelity history CSV (LLC→Fidelity, Traditional IRA→Fidelity SG IRA).",
    )
    parser.add_argument(
        "--fidelity-replace",
        action="store_true",
        help="With --import-fidelity-csv, delete existing trades for those funds first.",
    )
    parser.add_argument(
        "--import-fidelity-deposits",
        metavar="PATH",
        type=Path,
        help="Import Fidelity EFT/deposit cash events only (no trades).",
    )
    parser.add_argument(
        "--import-pdf-balances",
        metavar="PATH",
        type=Path,
        help="Import Portfolio Balances.pdf into a fund manual balance (requires --fund).",
    )
    parser.add_argument(
        "--fund",
        metavar="NAME",
        help="Fund name for --import-pdf-balances (e.g. Fidelity, Fidelity HSA, Super A).",
    )
    parser.add_argument(
        "--pdf-balances-dry-run",
        action="store_true",
        help="With --import-pdf-balances, parse and print without writing.",
    )
    parser.add_argument(
        "--import-fidelity-statements",
        metavar="PATH",
        nargs="+",
        type=Path,
        help="Import monthly Fidelity statement CSVs (Statement*.csv / HSAStatement*.csv).",
    )
    parser.add_argument(
        "--fidelity-statements-replace",
        action="store_true",
        help="With --import-fidelity-statements, replace existing Fidelity/HSA/IRA snapshots.",
    )
    parser.add_argument(
        "--import-aussuper-csv",
        metavar="PATH",
        type=Path,
        help="Import Australian Super CSV (AusSuperAG→Super A, AusSuperSG→Super S).",
    )
    parser.add_argument(
        "--aussuper-fund",
        metavar="NAME",
        help="Override fund name for --import-aussuper-csv (default from filename).",
    )
    parser.add_argument(
        "--aussuper-owner",
        metavar="OWNER",
        help="Override owner for --import-aussuper-csv (one of the owners in settings.json).",
    )
    parser.add_argument(
        "--aussuper-replace",
        action="store_true",
        help="With --import-aussuper-csv, delete existing trades for that fund first.",
    )
    parser.add_argument(
        "--import-aussuper-statements",
        metavar="PATH",
        nargs="+",
        type=Path,
        help="Import Australian Super annual statement PDFs (AusSuperAG→Super A).",
    )
    parser.add_argument(
        "--aussuper-statements-replace",
        action="store_true",
        help="With --import-aussuper-statements, replace existing Super A/S snapshots.",
    )
    parser.add_argument(
        "--import-hl-csv",
        metavar="PATH",
        type=Path,
        help="Import Hargreaves Lansdown history (HL_history.csv→UK Super, GBP).",
    )
    parser.add_argument(
        "--hl-fund",
        metavar="NAME",
        help="Override fund name for --import-hl-csv.",
    )
    parser.add_argument(
        "--hl-owner",
        metavar="OWNER",
        help="Override owner for --import-hl-csv (one of the owners in settings.json).",
    )
    parser.add_argument(
        "--hl-replace",
        action="store_true",
        help="With --import-hl-csv, delete existing trades for that fund first.",
    )
    parser.add_argument(
        "--import-hl-reports",
        metavar="PATH",
        nargs="+",
        type=Path,
        help="Import HL quarterly investment report PDFs (UK Super NAV + share prices).",
    )
    parser.add_argument(
        "--hl-reports-replace",
        action="store_true",
        help="With --import-hl-reports, replace existing UK Super snapshots and marks.",
    )
    parser.add_argument("--trade-gui", action="store_true", help="Open desktop GUI to enter trades.")
    parser.add_argument(
        "--serve-web",
        action="store_true",
        help="Run the portfolio manager web UI (FastAPI on WEB_HOST:WEB_PORT).",
    )
    parser.add_argument(
        "--refresh-live-quotes",
        action="store_true",
        help="Fetch current Yahoo Finance prices for portfolio tickers (see LIVE_QUOTES).",
    )
    parser.add_argument(
        "--quotes-owner",
        metavar="OWNER",
        help="With --refresh-live-quotes, limit to one owner from settings.json.",
    )
    parser.add_argument(
        "--check-option-strike-alerts",
        action="store_true",
        help="Email when an option underlying first trades within $1 of strike.",
    )
    parser.add_argument(
        "--option-strike-alerts-dry-run",
        action="store_true",
        help="With --check-option-strike-alerts, print matches without email or DB writes.",
    )
    parser.add_argument(
        "--option-strike-alerts-force",
        action="store_true",
        help="With --check-option-strike-alerts, run even when markets are closed.",
    )

    args = parser.parse_args()
    ran_any_flag = (
        args.snapshot_stocks_daily
        or args.snapshot_funds_weekly
        or args.pdf_report
        or args.email_holdings
        or args.market_status
        or args.import_funds_csv is not None
        or args.fund_add is not None
        or args.fund_list
        or args.trade_add
        or args.trade_list
        or args.trade_report
        or args.email_trade_report
        or args.trade_snapshot
        or args.import_trades_csv is not None
        or args.import_fidelity_csv is not None
        or args.import_fidelity_deposits is not None
        or args.import_pdf_balances is not None
        or args.import_fidelity_statements
        or args.import_aussuper_csv is not None
        or args.import_aussuper_statements
        or args.import_hl_csv is not None
        or args.import_hl_reports
        or args.refresh_live_quotes
        or args.check_option_strike_alerts
        or args.trade_gui
        or args.serve_web
    )

    if args.serve_web:
        import uvicorn

        host = os.getenv("WEB_HOST", "127.0.0.1")
        port = _parse_int_env("WEB_PORT", 8080)
        uvicorn.run("web.app:app", host=host, port=port)
        return

    if args.trade_gui:
        from trade_gui import main as trade_gui_main

        trade_gui_main()
        return

    if args.market_status:
        from ledger.market_calendar import (
            any_exchange_open_now,
            exchange_open_reason,
            is_au_market_open_now,
            is_au_trading_day,
            is_us_market_open_now,
            is_us_trading_day,
            market_holidays_enabled,
            should_poll_live_quotes,
            should_send_holdings_email,
            skip_reason,
        )

        print(skip_reason())
        print(exchange_open_reason())
        print(f"NYSE session: {'yes' if is_us_trading_day() else 'no'}")
        print(f"ASX session:  {'yes' if is_au_trading_day() else 'no'}")
        print(f"NYSE open now: {'yes' if is_us_market_open_now() else 'no'}")
        print(f"ASX open now:  {'yes' if is_au_market_open_now() else 'no'}")
        print(f"Holdings email allowed: {'yes' if should_send_holdings_email() else 'no'}")
        print(f"Live quote polls allowed: {'yes' if should_poll_live_quotes() else 'no'}")
        print(f"GUI auto-refresh active: {'yes' if should_poll_live_quotes() and any_exchange_open_now() else 'no'}")
        print(f"MARKET_HOLIDAYS_ENABLED: {market_holidays_enabled()}")
        return

    if not ran_any_flag:
        run_email_holdings()
        from ledger.market_calendar import should_send_holdings_email

        if should_send_holdings_email():
            print("Portfolio holdings email sent successfully.")
        return

    pdf_path_written: Path | None = None
    if args.snapshot_stocks_daily:
        run_snapshot_stocks_daily()

    if args.snapshot_funds_weekly:
        run_snapshot_funds_weekly()

    if args.import_funds_csv is not None:
        run_import_funds_csv(args.import_funds_csv)

    if args.fund_add is not None:
        trades_cli.run_fund_add(args.fund_add, args.fund_owner, args.fund_cash, args.fund_currency)
    if args.fund_list:
        trades_cli.run_fund_list()
    if args.trade_add:
        if not all([args.trade_fund, args.trade_side, args.trade_shares, args.trade_price]):
            raise SystemExit(
                "--trade-add requires --trade-fund, --trade-side, --trade-shares, --trade-price"
            )
        if args.trade_instrument == "stock" and not args.trade_ticker:
            raise SystemExit("--trade-add stock trades require --trade-ticker")
        if args.trade_instrument == "option" and not (
            args.trade_ticker
            and (
                is_occ_symbol(args.trade_ticker)
                or all([args.trade_expiry, args.trade_strike, args.trade_put_call])
            )
        ):
            raise SystemExit(
                "--trade-add option trades require --trade-ticker (OCC or underlying) plus "
                "--trade-expiry, --trade-strike, --trade-put-call when not using a full OCC symbol"
            )
        td = date.fromisoformat(args.trade_date) if args.trade_date else None
        opt_exp = date.fromisoformat(args.trade_expiry) if args.trade_expiry else None
        trades_cli.run_trade_add(
            args.trade_fund,
            args.trade_ticker or "",
            args.trade_side,
            args.trade_shares,
            args.trade_price,
            td,
            args.trade_fees,
            args.trade_notes or None,
            instrument_type=args.trade_instrument,
            expiry=opt_exp,
            strike=args.trade_strike,
            put_call=args.trade_put_call,
            contract_multiplier=args.trade_multiplier,
        )
    if args.trade_list:
        if not args.trade_fund:
            raise SystemExit("--trade-list requires --trade-fund")
        trades_cli.run_trade_list(args.trade_fund)
    if args.trade_snapshot:
        if not args.trade_fund:
            raise SystemExit("--trade-snapshot requires --trade-fund")
        trades_cli.run_trade_snapshot(args.trade_fund)
    if args.import_trades_csv is not None:
        trades_cli.run_import_trades_csv(args.import_trades_csv)

    if args.import_fidelity_csv is not None:
        trades_cli.run_import_fidelity_csv(args.import_fidelity_csv, replace_existing=args.fidelity_replace)

    if args.import_fidelity_deposits is not None:
        trades_cli.run_import_fidelity_deposits(args.import_fidelity_deposits)

    if args.import_pdf_balances is not None:
        if not args.fund:
            raise SystemExit("--import-pdf-balances requires --fund NAME")
        trades_cli.run_import_pdf_balances(
            args.import_pdf_balances,
            fund_name=args.fund,
            dry_run=args.pdf_balances_dry_run,
        )

    if args.import_fidelity_statements:
        from ledger.db import connect_db
        from importers.import_fidelity_statements import collect_statement_paths, import_fidelity_statements

        paths = collect_statement_paths(list(args.import_fidelity_statements))
        if not paths:
            raise SystemExit("No Fidelity statement CSV files found.")
        conn = connect_db(trades_cli._db_path())
        try:
            counts = import_fidelity_statements(
                conn,
                paths,
                replace=args.fidelity_statements_replace or True,
            )
        finally:
            conn.close()
        total = sum(counts.values())
        print(f"Imported {total} monthly snapshot(s) from {len(paths)} statement file(s):")
        for fund_name, n in sorted(counts.items()):
            print(f"  {fund_name}: {n} month(s)")

    if args.import_aussuper_csv is not None:
        trades_cli.run_import_aussuper_csv(
            args.import_aussuper_csv,
            replace_existing=args.aussuper_replace,
            fund_name=args.aussuper_fund,
            owner=args.aussuper_owner,
        )

    if args.import_aussuper_statements:
        trades_cli.run_import_aussuper_statements(
            args.import_aussuper_statements,
            replace=args.aussuper_statements_replace or True,
            fund_name=args.aussuper_fund,
            owner=args.aussuper_owner,
        )

    if args.import_hl_csv is not None:
        trades_cli.run_import_hl_csv(
            args.import_hl_csv,
            replace_existing=args.hl_replace,
            fund_name=args.hl_fund,
            owner=args.hl_owner,
        )

    if args.import_hl_reports:
        trades_cli.run_import_hl_reports(
            args.import_hl_reports,
            replace=args.hl_reports_replace or True,
        )

    if args.refresh_live_quotes:
        trades_cli.run_refresh_live_quotes(args.quotes_owner)

    if args.check_option_strike_alerts:
        from reports.option_strike_alerts import run_option_strike_alerts

        run_option_strike_alerts(
            dry_run=args.option_strike_alerts_dry_run,
            force=args.option_strike_alerts_force,
        )

    trade_plain_html: tuple[str, str, Path | None] | None = None
    if args.trade_report or args.email_trade_report:
        if not args.trade_fund:
            raise SystemExit("--trade-report requires --trade-fund")
        try:
            trade_plain_html = trades_cli.run_trade_report(args.trade_fund)
        except (ValueError, FileNotFoundError, RuntimeError) as e:
            raise SystemExit(f"Trade report failed: {e}") from None
        if args.trade_report:
            _plain, _html, pdf_path = trade_plain_html
            if pdf_path is not None:
                print(f"Trade report PDF written: {pdf_path}")
            if args.trade_report_text:
                print(_plain)
    if args.email_trade_report and trade_plain_html:
        subject = os.getenv("TRADE_EMAIL_SUBJECT", "Trade & fund report")
        old_subj = os.environ.get("EMAIL_SUBJECT")
        os.environ["EMAIL_SUBJECT"] = subject
        try:
            pdf_attach = trade_plain_html[2]
            attachments = [pdf_attach] if pdf_attach is not None and pdf_attach.is_file() else None
            send_email(trade_plain_html[0], trade_plain_html[1], attachments=attachments)
            print("Trade report email sent successfully.")
            if attachments:
                print(f"  PDF attached: {pdf_attach.name}")
        finally:
            if old_subj is not None:
                os.environ["EMAIL_SUBJECT"] = old_subj
            elif "EMAIL_SUBJECT" in os.environ:
                del os.environ["EMAIL_SUBJECT"]

    if args.pdf_report:
        pdf_path_written = run_pdf_report()

    holdings_attachments: list[Path] = []
    if args.email_holdings and _env_bool("EMAIL_ATTACH_PDF"):
        cand = _pdf_output_path()
        if cand.is_file():
            holdings_attachments.append(cand)

    if args.email_holdings:
        run_email_holdings(attach_pdf_paths=holdings_attachments or None)
        print("Portfolio holdings email sent successfully.")

    if _env_bool("EMAIL_ATTACH_PDF") and pdf_path_written is not None and pdf_path_written.is_file():
        if not args.email_holdings:
            plain = (
                "Portfolio PDF report is attached.\n\n"
                f"File: {pdf_path_written.name}\n"
            )
            html = (
                "<p>Portfolio PDF report is attached.</p>"
                f"<p><code>{html_module.escape(pdf_path_written.name)}</code></p>"
            )
            send_email(plain, html, attachments=[pdf_path_written])
            print("PDF notification email sent successfully.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as e:
        raise SystemExit(f"Error: {e}") from None
