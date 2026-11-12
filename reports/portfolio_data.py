"""Build portfolio holdings rows (no Tk) for the Current portfolio tab."""

from __future__ import annotations



from pathlib import Path

from reports.holdings_compare import collect_ledger_card_positions
from reports.portfolio_cards import HoldingCardData
from ledger.quotes import (
    listing_currency,
    live_quotes_enabled,
    normalize_ticker,
    price_in_fund_currency,
    resolve_day_move,
)

def _matches_port_market(market: str, market_sel: str) -> bool:
    sel = market_sel.strip()
    if not sel or sel == "All":
        return True
    m = str(market or "US").strip().upper()
    if sel.startswith("Austral"):
        return m == "AU"
    if sel.startswith("United") or sel == "US":
        return m == "US"
    return True


def _currency_loc(currency: str) -> str:
    c = str(currency or "").upper()
    if c == "AUD":
        return "AUS"
    return "US"


def _share_display_currency(pos) -> str:
    """Share location → AUD (AU) or USD (US and all other listings)."""
    m = str(pos.market or "").strip().upper()
    if m == "AU":
        return "AUD"
    return "USD"


def _totals_currency(
    fund_filter: str | None,
    positions,
    db_path: Path,
) -> str:
    """Fund scope → fund currency; owner/portfolio scope → USD."""
    if fund_filter:
        if positions:
            return (positions[0].currency or "USD").strip().upper()
        from ledger.db import connect_db
        from ledger.trades_db import get_fund_by_name, init_trade_schema

        conn = connect_db(db_path)
        try:
            init_trade_schema(conn)
            fund = get_fund_by_name(conn, fund_filter)
            if fund:
                return (fund.currency or "USD").strip().upper()
        finally:
            conn.close()
    return "USD"


def _fmt_money(amount: float, currency: str = "USD") -> str:
    c = str(currency or "USD").strip().upper()
    if c == "AUD":
        return f"A${amount:,.2f}"
    if c == "GBP":
        return f"£{amount:,.2f}"
    return f"${amount:,.2f}"


def _fmt_pct(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def _yahoo_quote_currency(ticker: str, *, market: str | None = None, fund_currency: str | None = None) -> str:
    """Currency Yahoo returns for this ticker (US listings → USD, ASX → AUD)."""
    return listing_currency(ticker, market=market, fund_currency=fund_currency)


def _quote_price_for_display(
    price: float | None,
    *,
    ticker: str,
    pos,
    fx_rates: dict[str, float],
) -> float | None:
    """Quote → listing/display currency (AUD for ASX, unchanged)."""
    if price is None:
        return None
    display_cur = _share_display_currency(pos)
    fund_cur = (pos.currency or "USD").strip().upper()
    quote_cur = _yahoo_quote_currency(ticker, market=pos.market, fund_currency=fund_cur)
    return price_in_fund_currency(float(price), quote_cur, display_cur, fx_rates)


def _fund_amount_for_display(
    amount: float | None,
    *,
    pos,
    fx_rates: dict[str, float],
) -> float | None:
    """Ledger amounts (fund currency) → listing/display currency."""
    if amount is None:
        return None
    fund_cur = (pos.currency or "USD").strip().upper()
    display_cur = _share_display_currency(pos)
    return price_in_fund_currency(float(amount), fund_cur, display_cur, fx_rates)


def _resolve_underlying_symbol(pos) -> str | None:
    if pos.instrument_type == "option":
        from ledger.option_symbols import parse_occ_symbol

        parsed = parse_occ_symbol(pos.ticker)
        if parsed:
            return parsed.underlying
    raw = (pos.underlying or "").strip().upper()
    return raw or None


def _append_quote_symbol(
    symbols: list[tuple[str, str]],
    seen: set[tuple[str, str]],
    ticker: str,
    *,
    market: str,
    fund_currency: str,
) -> None:
    key = (
        ticker,
        listing_currency(ticker, market=market, fund_currency=fund_currency),
    )
    if key in seen:
        return
    seen.add(key)
    symbols.append(key)


def load_portfolio_holdings(
    db_path: Path,
    *,
    owner: str,
    fund_filter: str | None,
    market_sel: str,
    use_live: bool,
    owner_label: str,
) -> tuple[list[HoldingCardData], str]:
    from ledger.db import connect_db

    live_note = " · live prices" if use_live else ""
    force_live = use_live and live_quotes_enabled(force=use_live)

    conn = connect_db(db_path)
    try:
        positions = collect_ledger_card_positions(
            conn,
            owner=owner,
            fund_name=fund_filter,
            live=use_live,
            force_live=force_live,
        )
    finally:
        conn.close()

    if market_sel and market_sel != "All":
        positions = [p for p in positions if _matches_port_market(p.market, market_sel)]

    symbol_list: list[tuple[str, str]] = []
    seen_symbols: set[tuple[str, str]] = set()
    for p in positions:
        fc = (p.currency or "USD").strip().upper()
        if p.mark_price is not None:
            _append_quote_symbol(
                symbol_list,
                seen_symbols,
                p.ticker,
                market=p.market,
                fund_currency=fc,
            )
        if p.instrument_type == "option":
            und = _resolve_underlying_symbol(p)
            if und:
                _append_quote_symbol(
                    symbol_list,
                    seen_symbols,
                    und,
                    market=p.market,
                    fund_currency=fc,
                )
    details_key = f"ledger:{owner}:{fund_filter or 'All'}:{market_sel}"
    quote_details: dict = {}
    if use_live:
        try:
            from ledger.quotes import fetch_quote_details

            if symbol_list:
                quote_details = fetch_quote_details(
                    symbol_list,
                    cache_key=details_key,
                    force=True,
                    with_sparklines=True,
                )
        except Exception:
            quote_details = {}
    elif symbol_list:
        from ledger.quotes import peek_quote_details_cache

        quote_details = peek_quote_details_cache(details_key)

    from ledger.quotes import fx_to_usd_rates

    fx_rates = fx_to_usd_rates(allow_network=force_live)
    totals_cur = _totals_currency(fund_filter, positions, db_path)

    prepared: list[tuple] = []
    for pos in positions:
        norm = normalize_ticker(pos.ticker)
        is_option = pos.instrument_type == "option"
        mult = pos.contract_multiplier if is_option else 1.0
        details = quote_details.get(norm)
        und_raw = _resolve_underlying_symbol(pos) if is_option else None
        und_norm = normalize_ticker(und_raw) if und_raw else None
        und_details = quote_details.get(und_norm) if und_norm else None
        stock_ticker = und_norm or norm
        stock_details = und_details if is_option else details
        display_cur = _share_display_currency(pos)
        loc = _currency_loc(display_cur)
        last = _fund_amount_for_display(pos.mark_price, pos=pos, fx_rates=fx_rates) or 0.0
        if details and details.price is not None:
            last = _quote_price_for_display(
                details.price,
                ticker=norm,
                pos=pos,
                fx_rates=fx_rates,
            ) or last
        underlying_last: float | None = None
        if is_option and und_details and und_details.price is not None:
            underlying_last = _quote_price_for_display(
                und_details.price,
                ticker=und_norm or norm,
                pos=pos,
                fx_rates=fx_rates,
            )
        spark = stock_details.sparkline if stock_details else None
        quote_last = (
            stock_details.price
            if stock_details and stock_details.price is not None
            else (underlying_last if is_option else last)
        )
        day_chg, day_pct, prev = resolve_day_move(
            stock_details, last=quote_last, sparkline=spark
        )
        if day_chg is not None and stock_details and stock_details.price is not None:
            converted_last = _quote_price_for_display(
                stock_details.price,
                ticker=stock_ticker,
                pos=pos,
                fx_rates=fx_rates,
            )
            if converted_last is not None and stock_details.price:
                day_chg = day_chg * (converted_last / float(stock_details.price))
        if prev is not None:
            prev = _quote_price_for_display(
                prev,
                ticker=stock_ticker,
                pos=pos,
                fx_rates=fx_rates,
            )
        avg_cost_display = _fund_amount_for_display(pos.avg_cost, pos=pos, fx_rates=fx_rates) or 0.0
        cost_basis = pos.shares * avg_cost_display * mult
        mkt = pos.shares * last * mult if last else (
            _fund_amount_for_display(pos.market_value, pos=pos, fx_rates=fx_rates) or 0.0
        )
        if pos.unrealized_pl is not None and not use_live:
            pl_d = _fund_amount_for_display(pos.unrealized_pl, pos=pos, fx_rates=fx_rates) or 0.0
            mkt = cost_basis + pl_d
        else:
            pl_d = mkt - cost_basis
        pl_p = (pl_d / cost_basis * 100.0) if cost_basis > 0 else 0.0
        prepared.append(
            (
                pos,
                norm,
                details,
                loc,
                display_cur,
                last,
                avg_cost_display,
                spark,
                day_chg,
                day_pct,
                prev,
                mkt,
                pl_d,
                pl_p,
                underlying_last,
            )
        )

    total_mkt = sum(
        price_in_fund_currency(row[11], row[4], totals_cur, fx_rates) for row in prepared
    )
    cards: list[HoldingCardData] = []
    for (
        pos,
        norm,
        details,
        loc,
        display_cur,
        last,
        avg_cost_display,
        spark,
        day_chg,
        day_pct,
        prev,
        mkt,
        pl_d,
        pl_p,
        underlying_last,
    ) in prepared:
        is_option = pos.instrument_type == "option"
        und_raw = _resolve_underlying_symbol(pos) if is_option else None
        und_norm = normalize_ticker(und_raw) if und_raw else None
        und_details = quote_details.get(und_norm) if und_norm else None
        stock_ticker = und_norm or norm
        stock_details = und_details if is_option else details
        mkt_for_total = price_in_fund_currency(mkt, display_cur, totals_cur, fx_rates)
        pct_port = mkt_for_total / total_mkt * 100.0 if total_mkt else 0.0

        def _cv(price: float | None) -> float | None:
            return _quote_price_for_display(
                price,
                ticker=stock_ticker,
                pos=pos,
                fx_rates=fx_rates,
            )

        day_open = _cv(stock_details.day_open) if stock_details else None
        day_high = _cv(stock_details.day_high) if stock_details else None
        day_low = _cv(stock_details.day_low) if stock_details else None
        if spark:
            converted_spark: list[float] = []
            for p in spark:
                cv = _cv(float(p))
                converted_spark.append(cv if cv is not None else float(p))
            spark = tuple(converted_spark)
            if day_open is None:
                day_open = spark[0]
            if day_high is None:
                day_high = max(spark)
            if day_low is None:
                day_low = min(spark)
        cards.append(
            HoldingCardData(
                ticker=pos.ticker,
                name=(
                    pos.display_name
                    or (details.name if details and details.name else pos.ticker)
                ),
                loc=loc,
                shares=pos.shares,
                avg_cost=avg_cost_display,
                last=last,
                day_change=day_chg,
                day_change_pct=day_pct,
                pct_port=pct_port,
                mkt_val=mkt,
                pl_d=pl_d,
                pl_p=pl_p,
                day_open=day_open,
                day_high=day_high,
                day_low=day_low,
                w52_high=_cv(stock_details.week52_high) if stock_details else None,
                w52_low=_cv(stock_details.week52_low) if stock_details else None,
                sparkline=spark,
                previous_close=prev,
                instrument_type=pos.instrument_type,
                underlying=und_raw or pos.underlying,
                strike=pos.strike,
                expiry=pos.expiry,
                put_call=pos.put_call,
                contract_multiplier=pos.contract_multiplier,
                underlying_last=underlying_last,
            )
        )

    cost = 0.0
    mkt = 0.0
    pl = 0.0
    for row in prepared:
        pos_row = row[0]
        share_cur = row[4]
        avg_cost_display = row[6]
        mkt_val = row[11]
        pl_d = row[12]
        mult = pos_row.contract_multiplier if pos_row.instrument_type == "option" else 1.0
        row_cost = avg_cost_display * pos_row.shares * mult
        cost += price_in_fund_currency(row_cost, share_cur, totals_cur, fx_rates)
        mkt += price_in_fund_currency(mkt_val, share_cur, totals_cur, fx_rates)
        pl += price_in_fund_currency(pl_d, share_cur, totals_cur, fx_rates)
    pl_pct = (pl / cost * 100.0) if cost else 0.0
    scope = fund_filter if fund_filter else f"{owner_label} (all funds)"
    market_note = "" if market_sel == "All" else f" · {market_sel} only"
    summary = (
        f"{scope}: {len(positions)} position(s){market_note} · "
        f"cost {_fmt_money(cost, totals_cur)} · value {_fmt_money(mkt, totals_cur)} · "
        f"P/L {_fmt_money(pl, totals_cur)} ({_fmt_pct(pl_pct)}){live_note}"
    )
    return cards, summary
