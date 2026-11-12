from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
import os
import re
import time
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ledger.db import connect_db
from ledger.trades_db import (
    TradeFund,
    fetch_mark_prices_for_owner,
    fetch_trades_for_fund,
    init_trade_schema,
    list_funds,
)

_ASX_PREFIX = re.compile(r"^ASX:", re.I)


def live_quotes_enabled(*, force: bool = False) -> bool:
    if os.getenv("LIVE_QUOTES", "true").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    from ledger.market_calendar import should_poll_live_quotes

    return should_poll_live_quotes(force=force)


def normalize_ticker(raw: str) -> str:
    s = str(raw).strip().upper()
    if _ASX_PREFIX.match(s):
        s = s.split(":", 1)[1]
    return s


def to_yahoo_symbol(ticker: str, listing_cur: str = "USD") -> str:
    """Map app ticker to Yahoo Finance symbol using listing currency (AUD → .AX)."""
    from ledger.option_symbols import is_occ_symbol

    t = normalize_ticker(ticker)
    if is_occ_symbol(t):
        return t
    cur = (listing_cur or "USD").strip().upper()
    if cur in ("AUS", "AU"):
        cur = "AUD"
    if cur == "AUD" and not t.endswith(".AX"):
        return f"{t}.AX"
    return t


def listing_currency(
    ticker: str,
    *,
    market: str | None = None,
    fund_currency: str | None = None,
) -> str:
    """Exchange listing currency for Yahoo quotes (AU → AUD, everything else → USD)."""
    m = str(market or "").strip().upper()
    if m == "AU":
        return "AUD"
    if m == "US":
        return "USD"
    inferred = infer_mark_currency(ticker)
    if inferred == "AUD":
        return "AUD"
    return "USD"


def currency_from_location(loc: str) -> str:
    loc = str(loc).strip().upper()
    if loc in ("AUS", "AUD", "AU"):
        return "AUD"
    return "USD"


@dataclass(frozen=True)
class QuoteDetails:
    price: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None
    day_change_pct: float | None = None
    day_change: float | None = None
    day_open: float | None = None
    previous_close: float | None = None
    name: str | None = None
    sparkline: tuple[float, ...] | None = None


def _as_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _price_from_ticker_info(info: dict) -> float | None:
    for key in (
        "regularMarketPrice",
        "lastPrice",
        "currentPrice",
        "postMarketPrice",
        "preMarketPrice",
    ):
        v = info.get(key)
        if v is not None and float(v) > 0:
            return float(v)
    return None


def _day_change_pct_from_info(info: dict) -> float | None:
    pct = _as_float(info.get("regularMarketChangePercent"))
    if pct is not None:
        return pct
    price = _price_from_ticker_info(info)
    # yfinance fast_info's "previousClose" is unreliable (seen stale/wrong values
    # while "regularMarketPreviousClose" was correct) -- prefer the latter.
    prev = _as_float(info.get("regularMarketPreviousClose") or info.get("previousClose"))
    if price is not None and prev is not None and prev > 0 and abs(price - prev) > 1e-9:
        return (price - prev) / prev * 100.0
    return None


def resolve_day_move(
    details: QuoteDetails | None,
    *,
    last: float | None = None,
    sparkline: tuple[float, ...] | None = None,
) -> tuple[float | None, float | None, float | None]:
    """Return (day_change, day_change_pct, previous_close) with fallbacks."""
    prev = details.previous_close if details else None
    chg = details.day_change if details else None
    pct = details.day_change_pct if details else None
    price = last
    if price is None and details and details.price is not None:
        price = details.price
    if sparkline:
        if prev is None and price is not None:
            prev = float(sparkline[0])
        if chg is None and price is not None and prev is not None:
            chg = price - prev
        elif chg is None and len(sparkline) >= 2:
            chg = float(sparkline[-1]) - float(sparkline[0])
            if prev is None:
                prev = float(sparkline[0])
    if chg is None and price is not None and prev is not None:
        chg = price - prev
    if pct is None and chg is not None and prev is not None and prev > 0:
        pct = chg / prev * 100.0
    elif pct is None and chg is not None and price is not None and abs(price - chg) > 1e-9:
        pct = chg / (price - chg) * 100.0
    return chg, pct, prev


def _details_from_fast_info(
    info: dict,
    *,
    name: str | None = None,
    sparkline: tuple[float, ...] | None = None,
) -> QuoteDetails:
    price = _price_from_ticker_info(info)
    # yfinance fast_info's "previousClose" is unreliable (seen stale/wrong values
    # while "regularMarketPreviousClose" was correct) -- prefer the latter.
    prev = _as_float(info.get("regularMarketPreviousClose") or info.get("previousClose"))
    day_chg = _as_float(info.get("regularMarketChange"))
    if day_chg is None and price is not None and prev is not None:
        day_chg = price - prev
    return QuoteDetails(
        price=price,
        day_high=_as_float(info.get("dayHigh")),
        day_low=_as_float(info.get("dayLow")),
        week52_high=_as_float(info.get("yearHigh") or info.get("fiftyTwoWeekHigh")),
        week52_low=_as_float(info.get("yearLow") or info.get("fiftyTwoWeekLow")),
        day_open=_as_float(info.get("open") or info.get("regularMarketOpen")),
        day_change_pct=_day_change_pct_from_info(info),
        day_change=day_chg,
        previous_close=prev,
        name=name,
        sparkline=sparkline,
    )


def _yahoo_batch(symbols: list[tuple[str, str]]):
    import yfinance as yf

    yahoo_to_app: dict[str, str] = {}
    for ticker, currency in symbols:
        app = normalize_ticker(ticker)
        yahoo = to_yahoo_symbol(app, currency)
        yahoo_to_app[yahoo] = app
    yahoo_list = sorted(yahoo_to_app.keys())
    batch = yf.Tickers(" ".join(yahoo_list)) if yahoo_list else None
    return yahoo_to_app, batch


def fetch_live_quotes(
    symbols: list[tuple[str, str]],
) -> dict[str, float]:
    """
    Fetch latest market prices for (ticker, currency) pairs.
    Returns {NORMALIZED_TICKER: price}. Requires yfinance.
    """
    if not symbols:
        return {}

    try:
        yahoo_to_app, batch = _yahoo_batch(symbols)
    except ImportError as e:
        raise ImportError(
            "Live quotes need yfinance: pip install yfinance"
        ) from e

    if batch is None:
        return {}

    _suppress_yfinance_noise()
    out: dict[str, float] = {}
    for yahoo, app in yahoo_to_app.items():
        try:
            t = batch.tickers[yahoo]
            price = _price_from_ticker_info(dict(getattr(t, "fast_info", {}) or {}))
            if price is None:
                hist = t.history(period="5d", interval="1d")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
            if price is not None and price > 0:
                out[app] = price
        except Exception:
            continue

    return out


_details_cache: dict[str, tuple[float, dict[str, QuoteDetails]]] = {}
_DETAILS_CACHE_TTL_SEC = 300.0
_sparkline_cache: dict[str, tuple[float, dict[str, tuple[float, ...]]]] = {}
_name_cache: dict[str, str] = {}


def clear_quote_details_cache() -> None:
    _details_cache.clear()
    _sparkline_cache.clear()


def _company_name_for_ticker(ticker_obj, app: str) -> str | None:
    if app in _name_cache:
        return _name_cache[app]
    name: str | None = None
    try:
        info = dict(getattr(ticker_obj, "fast_info", {}) or {})
        raw = str(info.get("shortName") or info.get("longName") or "").strip()
        if raw:
            name = raw
    except Exception:
        pass
    if name:
        _name_cache[app] = name
    return name


def fetch_intraday_sparklines(
    symbols: list[tuple[str, str]],
    *,
    cache_key: str | None = None,
    force: bool = False,
    ttl: float | None = None,
) -> dict[str, tuple[float, ...]]:
    """Intraday 5-minute closes for portfolio sparklines."""
    if not symbols:
        return {}

    ttl = ttl if ttl is not None else float(os.getenv("LIVE_QUOTES_CACHE_SEC", "55"))
    key = cache_key or "|".join(f"{t}:{c}" for t, c in sorted(symbols))
    now = time.time()
    if not force and key in _sparkline_cache:
        ts, cached = _sparkline_cache[key]
        if now - ts < ttl:
            return dict(cached)

    try:
        yahoo_to_app, _ = _yahoo_batch(symbols)
    except ImportError:
        return {}

    _suppress_yfinance_noise()
    out: dict[str, tuple[float, ...]] = {}
    import yfinance as yf

    yahoo_list = sorted(yahoo_to_app.keys())
    if not yahoo_list:
        return {}

    def _closes_from_hist(hist) -> tuple[float, ...] | None:
        if hist is None or hist.empty or "Close" not in hist.columns:
            return None
        closes = [float(x) for x in hist["Close"].dropna().tolist()]
        return tuple(closes) if len(closes) >= 2 else None

    try:
        if len(yahoo_list) == 1:
            yahoo = yahoo_list[0]
            closes = _closes_from_hist(yf.Ticker(yahoo).history(period="1d", interval="5m"))
            if closes:
                out[yahoo_to_app[yahoo]] = closes
        else:
            raw = yf.download(
                yahoo_list,
                period="1d",
                interval="5m",
                group_by="ticker",
                progress=False,
                threads=True,
            )
            if raw is not None and not raw.empty:
                multi = getattr(raw.columns, "nlevels", 1) > 1
                if len(yahoo_list) == 1 or not multi:
                    closes = _closes_from_hist(raw)
                    if closes:
                        out[yahoo_to_app[yahoo_list[0]]] = closes
                else:
                    for yahoo in yahoo_list:
                        try:
                            sub = raw[yahoo]
                        except (KeyError, TypeError):
                            continue
                        closes = _closes_from_hist(sub)
                        if closes:
                            out[yahoo_to_app[yahoo]] = closes
    except Exception:
        pass

    _sparkline_cache[key] = (now, out)
    return dict(out)


def peek_quote_details_cache(
    cache_key: str,
    *,
    ttl: float = _DETAILS_CACHE_TTL_SEC,
) -> dict[str, QuoteDetails]:
    """Return cached quote details only (no network)."""
    now = time.time()
    entry = _details_cache.get(cache_key)
    if not entry:
        return {}
    ts, cached = entry
    if now - ts >= ttl:
        return {}
    return dict(cached)


def fetch_quote_details(
    symbols: list[tuple[str, str]],
    *,
    cache_key: str | None = None,
    force: bool = False,
    ttl: float = _DETAILS_CACHE_TTL_SEC,
    with_sparklines: bool = True,
    allow_network: bool = True,
) -> dict[str, QuoteDetails]:
    """Yahoo day/52-week ranges, name, and optional intraday sparklines."""
    if not symbols:
        return {}

    key = cache_key or "|".join(f"{t}:{c}" for t, c in sorted(symbols))
    now = time.time()
    if not force and key in _details_cache:
        ts, cached = _details_cache[key]
        if now - ts < ttl:
            return dict(cached)

    if not allow_network:
        return {}

    try:
        yahoo_to_app, batch = _yahoo_batch(symbols)
    except ImportError:
        return {}

    if batch is None:
        return {}

    sparklines: dict[str, tuple[float, ...]] = {}
    if with_sparklines:
        sparklines = fetch_intraday_sparklines(
            symbols, cache_key=f"spark:{key}", force=force, ttl=ttl
        )

    _suppress_yfinance_noise()
    out: dict[str, QuoteDetails] = {}
    for yahoo, app in yahoo_to_app.items():
        try:
            t = batch.tickers[yahoo]
            info = dict(getattr(t, "fast_info", {}) or {})
            name = _company_name_for_ticker(t, app)
            out[app] = _details_from_fast_info(
                info,
                name=name,
                sparkline=sparklines.get(app),
            )
        except Exception:
            continue

    _details_cache[key] = (now, out)
    return dict(out)


# Per-owner cache so the GUI does not re-fetch Yahoo on every fund/tile.
_live_marks_cache: dict[str, tuple[float, dict[str, float]]] = {}
_LIVE_CACHE_TTL_SEC = float(os.getenv("LIVE_QUOTES_CACHE_SEC", "55"))


def clear_live_quotes_cache() -> None:
    _live_marks_cache.clear()
    clear_quote_details_cache()
    clear_fx_cache()


def fetch_live_marks_for_owner(
    db_path: Path,
    owner: str,
    *,
    force: bool = False,
    ttl: float = _LIVE_CACHE_TTL_SEC,
) -> dict[str, float]:
    """Cached Yahoo quotes for one owner (one network round-trip per TTL window)."""
    if not live_quotes_enabled(force=force):
        return {}

    key = owner.strip()
    now = time.time()
    if not force and key in _live_marks_cache:
        ts, marks = _live_marks_cache[key]
        if now - ts < ttl:
            return dict(marks)

    symbols = collect_quote_symbols(db_path, owner=key)
    if not symbols:
        return {}

    try:
        marks = fetch_live_quotes(symbols)
    except ImportError:
        return {}

    _live_marks_cache[key] = (now, marks)
    return dict(marks)


def _sheet_symbols(owner: str) -> list[tuple[str, str]]:
    try:
        from main import _prepare_holdings, load_portfolio_dataframe
    except Exception:
        return []
    try:
        work = _prepare_holdings(load_portfolio_dataframe())
    except Exception:
        return []
    sub = work[work["Portfolio"] == owner]
    out: list[tuple[str, str]] = []
    for _, row in sub.iterrows():
        loc = str(row.get("Location", "") or "").strip().upper()
        cur = "AUD" if loc in ("AUS", "AUD", "AU") else "USD"
        out.append((normalize_ticker(str(row["Ticker"])), cur))
    return out


def collect_quote_symbols(
    db_path,
    *,
    owner: str | None = None,
) -> list[tuple[str, str]]:
    """Tickers needing quotes from trade ledgers (+ sheet holdings for sheet-only owners)."""
    from pathlib import Path

    from ledger.trade_ledger import build_fund_ledger

    path = Path(db_path)
    conn = connect_db(path)
    try:
        init_trade_schema(conn)
        funds = list_funds(conn)
    finally:
        conn.close()

    if owner:
        funds = [f for f in funds if f.owner == owner]

    seen: set[tuple[str, str]] = set()
    symbols: list[tuple[str, str]] = []

    def add(ticker: str, currency: str) -> None:
        key = (normalize_ticker(ticker), currency.upper())
        if key not in seen and key[0]:
            seen.add(key)
            symbols.append(key)

    for fund in funds:
        conn = connect_db(path)
        try:
            trades = fetch_trades_for_fund(conn, fund.id)
            db_marks = fetch_mark_prices_for_owner(conn, fund.owner)
        finally:
            conn.close()
        try:
            from reports.holdings_compare import _position_market

            result = build_fund_ledger(fund, trades, db_marks, date.today(), strict=False)
            for p in result.positions:
                if p.shares > 0:
                    mkt = _position_market(
                        p.ticker,
                        fund.currency,
                        instrument_type=p.instrument_type,
                        underlying=p.underlying,
                    )
                    add(p.ticker, listing_currency(p.ticker, market=mkt))
        except Exception:
            pass

    if owner:
        owners = [owner]
    else:
        owners = [OWNER_A, OWNER_B, OWNER_C]

    for o in owners:
        if not any(f.owner == o for f in funds) and owner is None:
            continue
        if owner is None or o == owner:
            for ticker, cur in _sheet_symbols(o):
                add(ticker, cur)

    return symbols


def infer_mark_currency(raw_ticker: str) -> str:
    """Currency of a stored mark from its ticker key (e.g. asx:VAU → AUD)."""
    from ledger.option_symbols import is_occ_symbol

    raw = str(raw_ticker).strip().upper()
    if is_occ_symbol(raw):
        return "USD"
    if raw.startswith("ASX:") or raw.endswith(".AX") or raw.endswith(".AU"):
        return "AUD"
    return "USD"


def sheet_ticker_currencies(owner: str) -> dict[str, str]:
    """Normalized ticker → currency from the Google sheet Location column."""
    try:
        from main import _prepare_holdings, load_portfolio_dataframe

        work = _prepare_holdings(load_portfolio_dataframe())
        sub = work[work["Portfolio"].astype(str).str.strip() == owner.strip()]
    except Exception:
        return {}
    out: dict[str, str] = {}
    for _, row in sub.iterrows():
        norm = normalize_ticker(str(row["Ticker"]))
        if not norm:
            continue
        loc = str(row.get("Location", "") or "").strip().upper()
        out[norm] = currency_from_location(loc)
    return out


def price_in_fund_currency(
    price: float,
    mark_currency: str,
    fund_currency: str,
    fx_rates: dict[str, float] | None = None,
) -> float:
    """Convert a mark price into the fund's reporting currency."""
    mark_cur = (mark_currency or "USD").strip().upper()
    fund_cur = (fund_currency or "USD").strip().upper()
    if mark_cur == fund_cur:
        return float(price)
    if mark_cur in ("USD", "US") and fund_cur in ("USD", "US"):
        return float(price)
    if mark_cur in ("USD", "US"):
        return convert_from_usd(price, fund_cur, fx_rates)
    if fund_cur in ("USD", "US"):
        return convert_to_usd(price, mark_cur, fx_rates)
    usd = convert_to_usd(price, mark_cur, fx_rates)
    return convert_from_usd(usd, fund_cur, fx_rates)


def fetch_owner_marks_with_currency(
    conn,
    owner: str,
    *,
    live: bool = False,
    force_live: bool = False,
) -> dict[str, tuple[float, str]]:
    """Owner mark prices with native currency (sheet snapshots are not all USD)."""
    sheet_curs = sheet_ticker_currencies(owner)
    out: dict[str, tuple[float, str]] = {}
    for raw_ticker, price in fetch_mark_prices_for_owner(conn, owner).items():
        norm = normalize_ticker(raw_ticker)
        cur = infer_mark_currency(raw_ticker)
        if cur == "USD":
            cur = sheet_curs.get(norm, cur)
        out[norm] = (float(price), cur)

    if not live:
        return out

    db_path = Path(os.getenv("STORAGE_DB_PATH", "data/portfolio_history.sqlite3")).expanduser()
    symbols = collect_quote_symbols(db_path, owner=owner.strip())
    if not symbols:
        return out

    try:
        live_prices = fetch_live_quotes(symbols)
    except ImportError:
        return out

    for ticker, cur in symbols:
        norm = normalize_ticker(ticker)
        price = live_prices.get(norm)
        if price is not None:
            out[norm] = (float(price), cur.upper())
    return out


def lookup_mark_price(marks: dict[str, float] | None, ticker: str) -> float | None:
    """Resolve a mark whether keys are NVDA, asx:VAU, etc."""
    if not marks:
        return None
    key = normalize_ticker(ticker)
    if key in marks:
        return marks[key]
    upper = ticker.upper()
    if upper in marks:
        return marks[upper]
    for k, price in marks.items():
        if normalize_ticker(k) == key:
            return price
    return None


def fetch_mark_prices_merged(
    conn,
    owner: str,
    *,
    live: bool | None = None,
    force_live: bool = False,
) -> dict[str, float]:
    """Native-currency mark prices; live Yahoo quotes override DB snapshots when enabled."""
    if live is None:
        use_live = live_quotes_enabled(force=force_live)
    else:
        use_live = live
    owner_marks = fetch_owner_marks_with_currency(
        conn, owner, live=use_live, force_live=force_live
    )
    return {ticker: price for ticker, (price, _cur) in owner_marks.items()}


def marks_for_fund(
    conn,
    fund,
    as_of: date | None = None,
    *,
    live: bool = False,
    force_live: bool = False,
) -> dict[str, float]:
    """Mark prices for a fund ledger: owner quotes, fund currency, HL statement marks."""
    from bisect import bisect_right

    from ledger.trades_db import fetch_fund_ticker_marks

    as_of = as_of or date.today()
    fund_cur = (fund.currency or "USD").strip().upper()
    fx_rates = fx_to_usd_rates(allow_network=live or force_live)
    owner_marks = fetch_owner_marks_with_currency(
        conn, fund.owner, live=live, force_live=force_live
    )
    out = {
        normalize_ticker(ticker): price_in_fund_currency(price, mark_cur, fund_cur, fx_rates)
        for ticker, (price, mark_cur) in owner_marks.items()
    }
    fund_marks = fetch_fund_ticker_marks(conn, fund.id)
    if fund_marks:
        for ticker, (dates, prices) in fund_marks.items():
            if not dates:
                continue
            idx = bisect_right(dates, as_of) - 1
            if idx < 0 or prices[idx] <= 0:
                continue
            tk = ticker.upper()
            # A fund-specific mark only wins over a live owner quote when it's
            # actually the value *for* as_of (within a statement-cadence
            # window) -- otherwise it's a stale point-in-time entry from a
            # past reconciliation that would silently freeze a liquid ticker's
            # "current" price for months once no newer entry gets added.
            # Falls back to it anyway when there's no live quote at all, since
            # a stale price beats none for tickers live quotes don't cover.
            fresh = (as_of - dates[idx]).days <= 45
            if fresh or tk not in out:
                out[tk] = prices[idx]
    return out


_historical_close_cache: dict[str, dict[date, float]] = {}
_historical_miss: set[str] = set()
_HISTORICAL_CHUNK = 40


def clear_historical_close_cache() -> None:
    _historical_close_cache.clear()
    _historical_miss.clear()


def _historical_key(ticker: str, currency: str) -> str:
    app = normalize_ticker(ticker)
    return f"{app}:{(currency or 'USD').upper()}"


def _suppress_yfinance_noise() -> None:
    import logging

    for name in ("yfinance", "yfinance.base", "yfinance.scrapers"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def _historical_series_covers(key: str, start: date, end: date) -> bool:
    if key in _historical_miss:
        return True
    cached = _historical_close_cache.get(key)
    if not cached:
        return False
    return min(cached) <= start and max(cached) >= end


def _store_historical_series(key: str, series: dict[date, float]) -> None:
    if series:
        merged = dict(_historical_close_cache.get(key, {}))
        merged.update(series)
        _historical_close_cache[key] = merged
        _historical_miss.discard(key)
    else:
        _historical_miss.add(key)


def _parse_yfinance_download(
    hist,
    yahoo_to_key: dict[str, str],
) -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {key: {} for key in yahoo_to_key.values()}
    if hist is None or getattr(hist, "empty", True):
        return out

    if hasattr(hist.columns, "nlevels") and hist.columns.nlevels > 1:
        level0 = hist.columns.get_level_values(0)
        close = hist["Close"] if "Close" in level0 else hist.xs("Close", axis=1, level=0)
        if hasattr(close, "columns"):
            for yahoo in close.columns:
                key = yahoo_to_key.get(str(yahoo))
                if not key:
                    continue
                for idx, val in close[yahoo].dropna().items():
                    d = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                    out[key][d] = float(val)
        else:
            key = next(iter(yahoo_to_key.values()))
            for idx, val in close.dropna().items():
                d = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                out[key][d] = float(val)
    else:
        key = next(iter(yahoo_to_key.values()))
        for idx, val in hist["Close"].dropna().items():
            d = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
            out[key][d] = float(val)
    return out


def _download_historical_batch(
    symbols: list[tuple[str, str]],
    start: date,
    end: date,
) -> None:
    if not symbols:
        return
    try:
        import yfinance as yf
    except ImportError:
        for ticker, currency in symbols:
            _historical_miss.add(_historical_key(ticker, currency))
        return

    _suppress_yfinance_noise()
    yahoo_to_key: dict[str, str] = {}
    for ticker, currency in symbols:
        app = normalize_ticker(ticker)
        yahoo = to_yahoo_symbol(app, currency)
        yahoo_to_key[yahoo] = _historical_key(ticker, currency)

    fetch_start = (start - timedelta(days=7)).isoformat()
    fetch_end = (end + timedelta(days=1)).isoformat()
    yahoo_list = sorted(yahoo_to_key)

    for i in range(0, len(yahoo_list), _HISTORICAL_CHUNK):
        chunk = yahoo_list[i : i + _HISTORICAL_CHUNK]
        chunk_map = {y: yahoo_to_key[y] for y in chunk}
        try:
            hist = yf.download(
                " ".join(chunk),
                start=fetch_start,
                end=fetch_end,
                auto_adjust=True,
                progress=False,
                threads=True,
                group_by="column",
            )
        except Exception:
            hist = None
        parsed = _parse_yfinance_download(hist, chunk_map)
        for key in chunk_map.values():
            _store_historical_series(key, parsed.get(key, {}))


def _ensure_historical_series(
    ticker: str,
    currency: str,
    start: date,
    end: date,
) -> dict[date, float]:
    """Daily close prices keyed by date (cached per ticker/currency)."""
    key = _historical_key(ticker, currency)
    if _historical_series_covers(key, start, end):
        return dict(_historical_close_cache.get(key, {}))

    _download_historical_batch([(ticker, currency)], start, end)
    return dict(_historical_close_cache.get(key, {}))


def historical_close_on_or_before(
    ticker: str,
    currency: str,
    as_of: date,
    *,
    start_hint: date | None = None,
) -> float | None:
    start = start_hint or as_of
    series = _ensure_historical_series(ticker, currency, start, date.today())
    if not series:
        return None
    dates = sorted(d for d in series if d <= as_of)
    if not dates:
        return None
    return series[dates[-1]]


def prefetch_historical_closes(
    symbols: list[tuple[str, str]],
    start: date,
    end: date | None = None,
) -> None:
    end = end or date.today()
    pending: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ticker, currency in symbols:
        pair = (normalize_ticker(ticker), (currency or "USD").upper())
        if pair in seen:
            continue
        seen.add(pair)
        key = _historical_key(ticker, currency)
        if _historical_series_covers(key, start, end):
            continue
        pending.append(pair)

    if pending:
        _download_historical_batch(pending, start, end)


@dataclass(frozen=True)
class HistoricalMarkResolver:
    """Fast close-on-or-before lookups from prefetched Yahoo history."""

    _series: dict[str, tuple[tuple[date, ...], tuple[float, ...]]]

    @classmethod
    def build(
        cls,
        symbols: list[tuple[str, str]],
        start: date,
        end: date | None = None,
    ) -> HistoricalMarkResolver:
        end = end or date.today()
        prefetch_historical_closes(symbols, start, end)
        packed: dict[str, tuple[tuple[date, ...], tuple[float, ...]]] = {}
        for ticker, currency in symbols:
            key = _historical_key(ticker, currency)
            if key in packed:
                continue
            raw = _historical_close_cache.get(key, {})
            if not raw:
                packed[key] = ((), ())
                continue
            ordered = sorted(raw.items())
            packed[key] = (
                tuple(d for d, _ in ordered),
                tuple(px for _, px in ordered),
            )
        return cls(_series=packed)

    def mark(self, ticker: str, currency: str, as_of: date) -> float | None:
        key = _historical_key(ticker, currency)
        dates, prices = self._series.get(key, ((), ()))
        if not dates:
            return None
        idx = bisect_right(dates, as_of) - 1
        if idx < 0:
            return None
        raw = prices[idx]

        # to_yahoo_symbol() only ever fetches a genuinely AUD-native quote (the
        # .AX suffix) or falls back to the symbol's default (USD) listing — the
        # `currency` argument otherwise just becomes part of the cache key, with
        # no conversion applied. That's a no-op for USD/AUD funds holding
        # same-currency positions, but silently returns raw USD prices for any
        # other fund currency (e.g. a GBP fund holding US stocks), wildly
        # overstating their value. Convert from that same native currency here.
        fund_cur = (currency or "USD").strip().upper()
        native_cur = "AUD" if fund_cur in ("AUD", "AUS", "AU") else "USD"
        if native_cur == fund_cur:
            return raw
        return price_in_fund_currency(raw, native_cur, fund_cur, fx_to_usd_rates())


# FX rates: multiply local currency amount to get USD.
_fx_usd_cache: tuple[float, dict[str, float]] | None = None
_FX_CACHE_TTL_SEC = 3600.0
_FX_PAIRS = {"AUD": "AUDUSD=X", "GBP": "GBPUSD=X"}


def clear_fx_cache() -> None:
    global _fx_usd_cache
    _fx_usd_cache = None


def _fx_rates_from_env() -> dict[str, float]:
    return {
        "USD": 1.0,
        "US": 1.0,
        "AUD": float(os.getenv("FX_AUD_USD", "0.65")),
        "GBP": float(os.getenv("FX_GBP_USD", "1.27")),
    }


def fx_to_usd_rates(*, force: bool = False, allow_network: bool = True) -> dict[str, float]:
    """Return multipliers so: value_usd = value_local * rates[currency]."""
    global _fx_usd_cache
    now = time.time()
    if not force and _fx_usd_cache is not None:
        ts, rates = _fx_usd_cache
        if now - ts < _FX_CACHE_TTL_SEC:
            return dict(rates)

    if not allow_network:
        return _fx_rates_from_env()

    rates: dict[str, float] = {"USD": 1.0, "US": 1.0}
    try:
        import yfinance as yf

        for cur, yahoo in _FX_PAIRS.items():
            try:
                t = yf.Ticker(yahoo)
                info = dict(getattr(t, "fast_info", {}) or {})
                rate = _as_float(
                    info.get("lastPrice")
                    or info.get("regularMarketPrice")
                    or info.get("previousClose")
                )
                if rate and rate > 0:
                    rates[cur] = rate
            except Exception:
                continue
    except ImportError:
        pass

    rates.setdefault("AUD", float(os.getenv("FX_AUD_USD", "0.65")))
    rates.setdefault("GBP", float(os.getenv("FX_GBP_USD", "1.27")))

    _fx_usd_cache = (now, rates)
    return dict(rates)


def convert_to_usd(
    amount: float,
    currency: str,
    rates: dict[str, float] | None = None,
) -> float:
    cur = (currency or "USD").strip().upper()
    if cur in ("USD", "US", ""):
        return float(amount)
    r = (rates or fx_to_usd_rates()).get(cur)
    if r is None or r <= 0:
        return float(amount)
    return float(amount) * r


def convert_from_usd(
    amount_usd: float,
    currency: str,
    rates: dict[str, float] | None = None,
) -> float:
    """Convert a USD amount into fund/local currency (inverse of convert_to_usd)."""
    cur = (currency or "USD").strip().upper()
    if cur in ("USD", "US", ""):
        return float(amount_usd)
    r = (rates or fx_to_usd_rates()).get(cur)
    if r is None or r <= 0:
        return float(amount_usd)
    return float(amount_usd) / r


def sum_tiles_value_usd(tiles, *, force_fx: bool = False) -> float:
    """Sum heatmap tile values converted to USD."""
    rates = fx_to_usd_rates(force=force_fx)
    return sum(convert_to_usd(t.value, t.currency, rates) for t in tiles)
