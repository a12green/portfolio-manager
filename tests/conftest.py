"""Shared fixtures. Every test gets its own temporary database and offline prices."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import os

ROOT = Path(__file__).resolve().parent.parent
# The test client talks plain HTTP, so it cannot receive Secure cookies.
# The default (true) is tested separately in a subprocess.
os.environ.setdefault("WEB_SECURE_COOKIES", "false")
os.environ.setdefault("MPLBACKEND", "Agg")  # draw charts without a display
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "sample_data"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A fresh, empty app database with all schemas created."""
    from ledger.db import connect_db, init_schema
    from ledger.trades_db import init_trade_schema

    path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("STORAGE_DB_PATH", str(path))
    conn = connect_db(path)
    try:
        init_schema(conn)
        init_trade_schema(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def offline_fx(monkeypatch):
    """Replace the GBP/USD lookup with a fixed rate so no network call is made."""
    import importers.import_hl as import_hl

    monkeypatch.setattr(
        import_hl,
        "_gbp_usd_rates_for_dates",
        lambda dates: {d: 1.25 for d in dates},
    )


@pytest.fixture
def demo_db(db_path, offline_fx):
    """The database produced by the sample-data loader."""
    from sample_data.seed_demo_db import main as seed

    assert seed([str(db_path)]) == 0
    return db_path


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail fast if any test tries to reach a live service (Yahoo or SMTP)."""
    import socket

    def _blocked(*args, **kwargs):
        raise OSError("network access is disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# --- Yahoo Finance fake (shared) -----------------------------------------------

import pandas as pd  # noqa: E402

class _FakeTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.fast_info = {
            "lastPrice": 101.5,
            "previousClose": 100.0,
            "dayHigh": 102.0,
            "dayLow": 99.0,
            "open": 100.5,
            "currency": "USD",
            "shortName": f"{symbol} Corp",
        }
        self.info = {"currency": "USD", "shortName": f"{symbol} Corp", "regularMarketPrice": 101.5}

    def history(self, period: str = "5d", interval: str = "1d", **kwargs):
        idx = pd.bdate_range(end="2026-11-10", periods=6)
        close = [100.0, 100.5, 101.0, 100.8, 101.2, 101.5]
        return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1000] * 6}, index=idx)


class _FakeTickers:
    def __init__(self, symbols: str):
        self.tickers = {s: _FakeTicker(s) for s in symbols.split()}


def _fake_download(tickers, *args, **kwargs):
    """Shaped like yfinance's default multi-ticker output: columns are (field, ticker)."""
    idx = pd.bdate_range(end="2026-11-10", periods=6)
    names = tickers if isinstance(tickers, (list, tuple)) else str(tickers).split()
    close = [100.0, 100.5, 101.0, 100.8, 101.2, 101.5]
    columns = pd.MultiIndex.from_product([["Close", "Open", "High", "Low", "Volume"], names])
    data = {}
    for n in names:
        data[("Close", n)] = close
        data[("Open", n)] = close
        data[("High", n)] = close
        data[("Low", n)] = close
        data[("Volume", n)] = [1000] * 6
    return pd.DataFrame(data, index=idx, columns=columns)


_SCREEN_GAINERS = [
    {"symbol": "NVDA", "shortName": "NVIDIA", "regularMarketPrice": 180.0,
     "regularMarketChangePercent": 3.2, "regularMarketDayHigh": 181.0,
     "regularMarketDayLow": 175.0, "regularMarketOpen": 176.0, "currency": "USD"},
]
_SCREEN_LOSERS = [
    {"symbol": "XYZ", "shortName": "Example", "regularMarketPrice": 10.0,
     "regularMarketChangePercent": -4.1, "regularMarketDayHigh": 10.5,
     "regularMarketDayLow": 9.8, "regularMarketOpen": 10.4, "currency": "USD"},
]


def _fake_screen(query, *, count=20, sortField=None, sortAsc=False, **kwargs):
    return {"quotes": _SCREEN_LOSERS if sortAsc else _SCREEN_GAINERS}


@pytest.fixture(autouse=True)
def fake_yahoo(monkeypatch):
    """Every test gets the Yahoo fake, so the real yfinance library (and its SQLite cache) is never used."""
    import yfinance as yf

    monkeypatch.setattr(yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(yf, "Tickers", _FakeTickers)
    monkeypatch.setattr(yf, "download", _fake_download)
    monkeypatch.setattr(yf, "screen", _fake_screen, raising=False)
    from ledger import quotes

    for name in ("clear_live_quotes_cache", "clear_quote_details_cache", "clear_historical_close_cache", "clear_fx_cache"):
        getattr(quotes, name)()
    yield
    for name in ("clear_live_quotes_cache", "clear_quote_details_cache", "clear_historical_close_cache", "clear_fx_cache"):
        getattr(quotes, name)()




# --- Holdings file (local CSV, Stocks.csv layout) -----------------------------------

HOLDINGS_CSV = """Account,Stock,Symbol,Share Qty,Average Purchase Price,Current Price
Portfolio,Household A,,,,
,,NVDA,10,150,180
,,AAPL,5,190,200
Portfolio,Household B,,,,
,,MSFT,3,400,420
"""


@pytest.fixture
def holdings_csv(monkeypatch, tmp_path):
    """Point the app at a synthetic holdings file. No real holdings are used."""
    path = tmp_path / "Stocks.csv"
    path.write_text(HOLDINGS_CSV, encoding="utf-8")
    monkeypatch.setenv("PORTFOLIO_CSV_PATH", str(path))
    return path
