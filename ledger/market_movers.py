from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

Market = Literal["US", "AU"]

_CACHE: dict[tuple[str, str], tuple[float, list[MarketMover]]] = {}
_CACHE_TTL_SEC = 300.0
_TOP_N = 20

# Yahoo screener thresholds aligned with major-board day-mover lists.
_US_GAIN_PCT = 3.0
_US_LOSS_PCT = -2.5
_US_MIN_MCAP = 2_000_000_000
_US_MIN_PRICE = 5.0
_US_MIN_VOLUME_GAIN = 150_000
_US_MIN_VOLUME_LOSS = 200_000

_AU_GAIN_PCT = 2.5
_AU_LOSS_PCT = -2.5
_AU_MIN_MCAP = 500_000_000
_AU_MIN_PRICE = 1.0
_AU_MIN_VOLUME = 100_000


@dataclass(frozen=True)
class MarketMover:
    rank: int
    symbol: str
    name: str
    price: float | None
    change: float | None
    change_pct: float | None
    volume: int | None


def clear_market_movers_cache() -> None:
    _CACHE.clear()


def _display_symbol(symbol: str, market: Market) -> str:
    sym = str(symbol or "").strip().upper()
    if market == "AU" and sym.endswith(".AX"):
        return sym[:-3]
    return sym


def _parse_quote(q: dict, rank: int, market: Market) -> MarketMover:
    vol = q.get("regularMarketVolume")
    return MarketMover(
        rank=rank,
        symbol=_display_symbol(q.get("symbol", ""), market),
        name=str(q.get("shortName") or q.get("longName") or q.get("symbol") or "").strip(),
        price=_as_float(q.get("regularMarketPrice")),
        change=_as_float(q.get("regularMarketChange")),
        change_pct=_as_float(q.get("regularMarketChangePercent")),
        volume=int(vol) if vol is not None else None,
    )


def _as_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_screen(query, *, count: int, sort_field: str, sort_asc: bool) -> list[dict]:
    import yfinance as yf

    result = yf.screen(
        query,
        count=count,
        sortField=sort_field,
        sortAsc=sort_asc,
    )
    quotes = result.get("quotes") or []
    return list(quotes)[:count]


def _us_movers_query(*, gainers: bool):
    from yfinance import EquityQuery as EQ

    pct = _US_GAIN_PCT if gainers else _US_LOSS_PCT
    pct_op = "gt" if gainers else "lt"
    min_vol = _US_MIN_VOLUME_GAIN if gainers else _US_MIN_VOLUME_LOSS
    return EQ(
        "and",
        [
            EQ(pct_op, ["percentchange", pct]),
            EQ("eq", ["region", "us"]),
            EQ("is-in", ["exchange", "NMS", "NYQ"]),
            EQ("gte", ["intradaymarketcap", _US_MIN_MCAP]),
            EQ("gte", ["intradayprice", _US_MIN_PRICE]),
            EQ("gt", ["dayvolume", min_vol]),
        ],
    )


def _au_movers_query(*, gainers: bool):
    from yfinance import EquityQuery as EQ

    pct = _AU_GAIN_PCT if gainers else _AU_LOSS_PCT
    pct_op = "gt" if gainers else "lt"
    return EQ(
        "and",
        [
            EQ(pct_op, ["percentchange", pct]),
            EQ("eq", ["region", "au"]),
            EQ("is-in", ["exchange", "ASX"]),
            EQ("gte", ["intradaymarketcap", _AU_MIN_MCAP]),
            EQ("gte", ["intradayprice", _AU_MIN_PRICE]),
            EQ("gt", ["dayvolume", _AU_MIN_VOLUME]),
        ],
    )


def _movers_query(market: Market, *, gainers: bool):
    if market == "US":
        return _us_movers_query(gainers=gainers)
    return _au_movers_query(gainers=gainers)


def fetch_market_movers(
    market: Market,
    *,
    top_n: int = _TOP_N,
    force: bool = False,
) -> tuple[list[MarketMover], list[MarketMover]]:
    """
    Top day gainers and losers from Yahoo Finance screener (major markets only).

    US: NYSE + NASDAQ, market cap ≥ US$2B, price ≥ US$5.
    AU: ASX only, market cap ≥ A$500M, price ≥ A$1.
    """
    market = market.upper()  # type: ignore[assignment]
    if market not in ("US", "AU"):
        raise ValueError("market must be US or AU")

    cache_key_g = (market, "gainers")
    cache_key_l = (market, "losers")
    now = time.time()

    if not force:
        g_cached = _CACHE.get(cache_key_g)
        l_cached = _CACHE.get(cache_key_l)
        if g_cached and l_cached and now - g_cached[0] < _CACHE_TTL_SEC and now - l_cached[0] < _CACHE_TTL_SEC:
            return g_cached[1], l_cached[1]

    gain_raw = _fetch_screen(
        _movers_query(market, gainers=True),
        count=top_n,
        sort_field="percentchange",
        sort_asc=False,
    )
    lose_raw = _fetch_screen(
        _movers_query(market, gainers=False),
        count=top_n,
        sort_field="percentchange",
        sort_asc=True,
    )

    gainers = [_parse_quote(q, i + 1, market) for i, q in enumerate(gain_raw)]
    losers = [_parse_quote(q, i + 1, market) for i, q in enumerate(lose_raw)]

    _CACHE[cache_key_g] = (now, gainers)
    _CACHE[cache_key_l] = (now, losers)
    return gainers, losers
