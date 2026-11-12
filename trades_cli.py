from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from ledger.db import connect_db
from importers.import_aussuper import import_aussuper_csv
from importers.import_fidelity import import_fidelity_csv
from importers.import_hl import import_hl_csv
from importers.import_trades import import_trades_csv
from ledger.trade_ledger import build_fund_ledger
from reports.trade_report import build_trade_report
from ledger.trades_db import (
    create_fund,
    fetch_fund_performance_history,
    fetch_mark_prices_for_owner,
    fetch_trades_for_fund,
    get_fund_by_name,
    init_trade_schema,
    list_funds,
    upsert_fund_performance_snapshot,
)


def _db_path() -> Path:
    return Path(os.getenv("STORAGE_DB_PATH", "data/portfolio_history.sqlite3")).expanduser()


def run_fund_add(name: str, owner: str, opening_cash: float, currency: str) -> None:
    from config import OWNERS

    if owner not in OWNERS:
        raise ValueError(f"Unknown owner {owner!r}. Use one of: {', '.join(OWNERS)}")
    conn = connect_db(_db_path())
    try:
        init_trade_schema(conn)
        if get_fund_by_name(conn, name):
            raise ValueError(f"Fund {name!r} already exists.")
        fund = create_fund(conn, name, owner, opening_cash, currency)
        print(f"Created fund {fund.name!r} (owner={fund.owner}, cash={fund.opening_cash:,.2f} {fund.currency})")
    finally:
        conn.close()


def run_fund_list() -> None:
    conn = connect_db(_db_path())
    try:
        funds = list_funds(conn)
        if not funds:
            print("No funds yet. Use --fund-add to create one.")
            return
        for f in funds:
            print(f"  [{f.id}] {f.name} — {f.owner}, {f.currency}, opening cash {f.opening_cash:,.2f}")
    finally:
        conn.close()


def run_trade_add(
    fund_name: str,
    ticker: str,
    side: str,
    shares: float,
    price: float,
    trade_date: date | None,
    fees: float,
    notes: str | None,
    *,
    instrument_type: str = "stock",
    underlying: str | None = None,
    expiry: date | None = None,
    strike: float | None = None,
    put_call: str | None = None,
    contract_multiplier: float | None = None,
) -> None:
    conn = connect_db(_db_path())
    try:
        init_trade_schema(conn)
        fund = get_fund_by_name(conn, fund_name)
        if not fund:
            raise ValueError(f"Unknown fund {fund_name!r}. Use --fund-add first.")
        from ledger.trades_db import insert_trade

        tid = insert_trade(
            conn,
            fund.id,
            trade_date or date.today(),
            ticker,
            side,
            shares,
            price,
            fees,
            notes,
            instrument_type=instrument_type,
            underlying=underlying,
            expiry=expiry,
            strike=strike,
            put_call=put_call,
            contract_multiplier=contract_multiplier,
        )
        label = "contracts" if instrument_type == "option" else "shares"
        print(
            f"Recorded trade #{tid}: {side} {shares} {label} {ticker} @ {price} "
            f"in fund {fund_name!r}"
        )
    finally:
        conn.close()


def run_trade_list(fund_name: str) -> None:
    conn = connect_db(_db_path())
    try:
        fund = get_fund_by_name(conn, fund_name)
        if not fund:
            raise ValueError(f"Unknown fund {fund_name!r}")
        trades = fetch_trades_for_fund(conn, fund.id)
        if not trades:
            print(f"No trades for fund {fund_name!r}")
            return
        for t in trades:
            qty = "contracts" if t.is_option else "shares"
            extra = ""
            if t.is_option:
                from ledger.option_symbols import display_option_label

                extra = f"  [{display_option_label(occ_symbol=t.ticker)}]"
            print(
                f"  #{t.id} {t.trade_date} {t.side:4} {t.shares:>8} {qty} "
                f"{t.ticker:<20} @ {t.price:>8.2f}  fees {t.fees:.2f}{extra}"
            )
    finally:
        conn.close()


def trade_report_pdf_path(fund_name: str, as_of: date | None = None) -> Path:
    import os
    import re

    safe = re.sub(r"[^\w\-]+", "_", fund_name.strip()).strip("_") or "fund"
    base = Path(os.getenv("TRADE_REPORT_DIR", "reports")).expanduser()
    return base / f"trade_{safe}_{(as_of or date.today()).isoformat()}.pdf"


def run_trade_report(
    fund_name: str,
    as_of: date | None = None,
    *,
    pdf_path: Path | None = None,
    write_pdf: bool = True,
) -> tuple[str, str, Path | None]:
    conn = connect_db(_db_path())
    try:
        fund = get_fund_by_name(conn, fund_name)
        if not fund:
            raise ValueError(f"Unknown fund {fund_name!r}")
        from ledger.market_calendar import should_poll_live_quotes, skip_reason
        from ledger.quotes import fetch_mark_prices_merged, live_quotes_enabled

        as_of = as_of or date.today()
        marks = fetch_mark_prices_merged(conn, fund.owner)
        if not marks:
            print(
                f"Note: no mark prices for owner {fund.owner!r}; "
                "run --snapshot-stocks-daily or enable LIVE_QUOTES."
            )
        elif live_quotes_enabled():
            print(f"Using live market quotes (Yahoo Finance) for {fund.owner!r}.")
        elif not should_poll_live_quotes():
            print(f"Live quotes skipped: {skip_reason()}")
        trades = fetch_trades_for_fund(conn, fund.id)
        result = build_fund_ledger(fund, trades, marks, as_of, strict=False)
        hist = fetch_fund_performance_history(conn, fund.id)
        plain, html = build_trade_report(result, hist)
        written: Path | None = None
        if write_pdf:
            from reports.trade_report_pdf import render_trade_report_pdf

            outp = pdf_path or trade_report_pdf_path(fund_name, as_of)
            written = render_trade_report_pdf(result, hist, outp)
        return plain, html, written
    finally:
        conn.close()


def run_trade_snapshot(fund_name: str, as_of: date | None = None) -> None:
    as_of = as_of or date.today()
    conn = connect_db(_db_path())
    try:
        fund = get_fund_by_name(conn, fund_name)
        if not fund:
            raise ValueError(f"Unknown fund {fund_name!r}")
        from ledger.quotes import fetch_mark_prices_merged

        marks = fetch_mark_prices_merged(conn, fund.owner)
        trades = fetch_trades_for_fund(conn, fund.id)
        result = build_fund_ledger(fund, trades, marks, as_of, strict=False)
        upsert_fund_performance_snapshot(
            conn,
            as_of,
            fund.id,
            result.cash_balance,
            result.positions_value,
            result.nav,
            result.invested_capital,
            result.cumulative_realized_pl,
            result.fund_roi_pct,
        )
        print(f"Saved fund performance snapshot for {fund_name!r} on {as_of.isoformat()}")
        hist = fetch_fund_performance_history(conn, fund.id)
        if len(hist) >= 2:
            print(f"  {len(hist)} snapshots stored — use for fund performance over time.")
    finally:
        conn.close()


def run_import_trades_csv(path: Path) -> int:
    conn = connect_db(_db_path())
    try:
        n = import_trades_csv(conn, path)
        print(f"Imported {n} trade(s) from {path.resolve()}")
        return n
    finally:
        conn.close()


def run_import_aussuper_csv(
    path: Path,
    replace_existing: bool = False,
    fund_name: str | None = None,
    owner: str | None = None,
) -> int:
    conn = connect_db(_db_path())
    try:
        from importers.import_aussuper import resolve_aussuper_profile

        fname, fowner = resolve_aussuper_profile(path, fund_name=fund_name, owner=owner)
        result = import_aussuper_csv(
            conn,
            path,
            fund_name=fname,
            owner=fowner,
            replace_existing=replace_existing,
        )
        print(
            f"Imported into {fname!r} (owner={fowner}) from {path.resolve()}: "
            f"{result.trades} trade(s), {result.cash_events} cash event(s), "
            f"{result.snapshots} balance snapshot(s)"
        )
        if result.opening_cash > 0:
            print(f"  Opening cash: ${result.opening_cash:,.2f}")
        return result.trades
    finally:
        conn.close()


def run_import_aussuper_statements(
    paths: list[Path],
    *,
    replace: bool = True,
    fund_name: str | None = None,
    owner: str | None = None,
) -> dict[str, int]:
    from importers.import_aussuper_statements import import_aussuper_statements

    conn = connect_db(_db_path())
    try:
        counts = import_aussuper_statements(
            conn,
            paths,
            fund_name=fund_name,
            owner=owner,
            replace=replace,
        )
        total = sum(counts.values())
        print(f"Imported {total} Australian Super snapshot(s) from {len(paths)} path(s):")
        for name, n in sorted(counts.items()):
            print(f"  {name}: {n}")
        return counts
    finally:
        conn.close()


def run_import_hl_csv(
    path: Path,
    replace_existing: bool = False,
    fund_name: str | None = None,
    owner: str | None = None,
) -> int:
    conn = connect_db(_db_path())
    try:
        from importers.import_hl import resolve_hl_profile

        fname, fowner = resolve_hl_profile(path, fund_name=fund_name, owner=owner)
        n = import_hl_csv(
            conn,
            path,
            fund_name=fname,
            owner=fowner,
            replace_existing=replace_existing,
        )
        print(
            f"Imported {n} HL trade(s) into {fname!r} (owner={fowner}, GBP) "
            f"from {path.resolve()}"
        )
        return n
    finally:
        conn.close()


def run_import_hl_reports(paths: list[Path], replace: bool = True) -> dict[str, int]:
    from importers.import_hl_reports import import_hl_investment_reports

    conn = connect_db(_db_path())
    try:
        counts = import_hl_investment_reports(conn, paths, replace=replace)
        print(
            f"Imported {counts.get('snapshots', 0)} UK Super snapshot(s) and "
            f"{counts.get('marks', 0)} historical price mark(s) from {len(paths)} path(s)."
        )
        return counts
    finally:
        conn.close()


def run_refresh_live_quotes(owner: str | None = None) -> None:
    from ledger.quotes import (
        clear_live_quotes_cache,
        collect_quote_symbols,
        fetch_live_quotes,
        fetch_live_marks_for_owner,
        normalize_ticker,
    )

    owner_arg = owner.strip() if owner else None
    clear_live_quotes_cache()
    symbols = collect_quote_symbols(_db_path(), owner=owner_arg)
    if not symbols:
        print("No tickers found for live quotes.")
        return
    print(f"Fetching {len(symbols)} ticker(s) from Yahoo Finance…")
    if owner_arg:
        prices = fetch_live_marks_for_owner(_db_path(), owner_arg, force=True)
    else:
        prices = fetch_live_quotes(symbols)
    if not prices:
        print("No prices returned (market closed, unknown symbol, or network issue).")
        return
    for sym in sorted(prices, key=lambda s: s):
        print(f"  {sym:8} {prices[sym]:>12.2f}")
    missing = {normalize_ticker(t) for t, _ in symbols} - set(prices)
    if missing:
        print(f"  (no quote: {', '.join(sorted(missing))})")
    print(f"Got {len(prices)} live quote(s). GUI heat map uses these when LIVE_QUOTES=true.")


def run_import_fidelity_csv(path: Path, replace_existing: bool = False) -> dict[str, int]:
    conn = connect_db(_db_path())
    try:
        counts = import_fidelity_csv(conn, path, replace_existing=replace_existing)
        total = sum(counts.values())
        print(f"Imported {total} Fidelity trade(s) from {path.resolve()}:")
        for name, n in sorted(counts.items()):
            print(f"  {name}: {n}")
        return counts
    finally:
        conn.close()


def run_import_fidelity_deposits(path: Path) -> dict[str, int]:
    from importers.import_fidelity import import_fidelity_deposits

    conn = connect_db(_db_path())
    try:
        counts = import_fidelity_deposits(conn, path)
        total = sum(counts.values())
        print(f"Imported {total} Fidelity cash event(s) from {path.resolve()}:")
        for name, n in sorted(counts.items()):
            print(f"  {name}: {n}")
        return counts
    finally:
        conn.close()


def run_import_pdf_balances(
    path: Path,
    *,
    fund_name: str,
    dry_run: bool = False,
) -> None:
    from importers.import_pdf_balances import _print_result, run_import_portfolio_balances

    if not str(fund_name or "").strip():
        raise SystemExit("--fund is required with --import-pdf-balances")
    parsed = run_import_portfolio_balances(path, fund_name=fund_name, dry_run=dry_run)
    _print_result(parsed, dry_run=dry_run)
