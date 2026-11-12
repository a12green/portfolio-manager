from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


_TRADE_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_funds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    opening_cash REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    shares REAL NOT NULL,
    price REAL NOT NULL,
    fees REAL NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (fund_id) REFERENCES trade_funds(id)
);

CREATE INDEX IF NOT EXISTS idx_trades_fund_date ON trades (fund_id, trade_date);

CREATE TABLE IF NOT EXISTS fund_performance_snapshots (
    snapshot_date TEXT NOT NULL,
    fund_id INTEGER NOT NULL,
    cash_balance REAL NOT NULL,
    positions_value REAL NOT NULL,
    nav REAL NOT NULL,
    invested_capital REAL NOT NULL,
    cumulative_realized_pl REAL NOT NULL,
    roi_pct REAL,
    PRIMARY KEY (snapshot_date, fund_id),
    FOREIGN KEY (fund_id) REFERENCES trade_funds(id)
);

CREATE TABLE IF NOT EXISTS fund_manual_balances (
    fund_id INTEGER PRIMARY KEY,
    balance_date TEXT NOT NULL,
    nav REAL NOT NULL,
    cash_balance REAL,
    positions_value REAL,
    notes TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (fund_id) REFERENCES trade_funds(id)
);

CREATE TABLE IF NOT EXISTS fund_cash_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id INTEGER NOT NULL,
    event_date TEXT NOT NULL,
    amount REAL NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (fund_id) REFERENCES trade_funds(id)
);

CREATE INDEX IF NOT EXISTS idx_fund_cash_events_fund_date
    ON fund_cash_events (fund_id, event_date);

CREATE TABLE IF NOT EXISTS fund_reported_cash (
    fund_id INTEGER PRIMARY KEY,
    balance_date TEXT NOT NULL,
    cash_balance REAL NOT NULL,
    source TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (fund_id) REFERENCES trade_funds(id)
);

CREATE TABLE IF NOT EXISTS fund_ticker_marks (
    fund_id INTEGER NOT NULL,
    mark_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'GBP',
    source TEXT,
    PRIMARY KEY (fund_id, mark_date, ticker),
    FOREIGN KEY (fund_id) REFERENCES trade_funds(id)
);

CREATE INDEX IF NOT EXISTS idx_fund_ticker_marks_fund_date
    ON fund_ticker_marks (fund_id, mark_date);

CREATE TABLE IF NOT EXISTS option_strike_near_alerts (
    occ_symbol TEXT PRIMARY KEY,
    underlying TEXT NOT NULL,
    strike REAL NOT NULL,
    expiry TEXT NOT NULL,
    put_call TEXT,
    notified_at TEXT NOT NULL,
    underlying_price REAL NOT NULL,
    distance REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_other_assets (
    owner TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    notes TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner, asset_type)
);
"""

# Default labels offered in the UI; any string is accepted (not schema-enforced).
OWNER_OTHER_ASSET_TYPES: tuple[str, ...] = ("Bank accounts", "Property")


@dataclass(frozen=True)
class TradeFund:
    id: int
    name: str
    owner: str
    currency: str
    opening_cash: float


@dataclass(frozen=True)
class FundCashEvent:
    id: int
    fund_id: int
    event_date: date
    amount: float
    notes: str | None


@dataclass(frozen=True)
class FundReportedCash:
    fund_id: int
    balance_date: date
    cash_balance: float
    source: str | None


@dataclass(frozen=True)
class FundManualBalance:
    fund_id: int
    balance_date: date
    nav: float
    cash_balance: float | None
    positions_value: float | None
    notes: str | None
    updated_at: str


@dataclass(frozen=True)
class OwnerOtherAsset:
    owner: str
    asset_type: str
    amount: float
    currency: str
    notes: str | None
    updated_at: str


@dataclass(frozen=True)
class TradeRecord:
    id: int
    fund_id: int
    trade_date: date
    ticker: str
    side: str
    shares: float
    price: float
    fees: float
    notes: str | None
    instrument_type: str = "stock"
    underlying: str | None = None
    expiry: date | None = None
    strike: float | None = None
    put_call: str | None = None
    contract_multiplier: float = 1.0

    @property
    def is_option(self) -> bool:
        return self.instrument_type == "option"


def _migrate_trade_options_columns(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(trades)")
    cols = {row[1] for row in cur.fetchall()}
    additions = (
        ("instrument_type", "TEXT NOT NULL DEFAULT 'stock'"),
        ("underlying", "TEXT"),
        ("expiry", "TEXT"),
        ("strike", "REAL"),
        ("put_call", "TEXT"),
        ("contract_multiplier", "REAL NOT NULL DEFAULT 1"),
    )
    for col, ddl in additions:
        if col not in cols:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {ddl}")
    conn.commit()


def _row_to_trade_record(row: tuple) -> TradeRecord:
    expiry_raw = row[11] if len(row) > 11 else None
    expiry = date.fromisoformat(expiry_raw) if expiry_raw else None
    return TradeRecord(
        id=int(row[0]),
        fund_id=int(row[1]),
        trade_date=date.fromisoformat(row[2]),
        ticker=str(row[3]),
        side=str(row[4]),
        shares=float(row[5]),
        price=float(row[6]),
        fees=float(row[7]),
        notes=row[8],
        instrument_type=str(row[9] or "stock") if len(row) > 9 else "stock",
        underlying=row[10] if len(row) > 10 else None,
        expiry=expiry,
        strike=float(row[12]) if len(row) > 12 and row[12] is not None else None,
        put_call=row[13] if len(row) > 13 else None,
        contract_multiplier=float(row[14] or 1) if len(row) > 14 else 1.0,
    )


def init_trade_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_TRADE_SCHEMA)
    _migrate_trade_options_columns(conn)
    conn.commit()


def list_funds(conn: sqlite3.Connection) -> list[TradeFund]:
    init_trade_schema(conn)
    cur = conn.execute(
        "SELECT id, name, owner, currency, opening_cash FROM trade_funds ORDER BY name"
    )
    return [TradeFund(int(r[0]), r[1], r[2], r[3], float(r[4])) for r in cur.fetchall()]


def get_fund_by_name(conn: sqlite3.Connection, name: str) -> TradeFund | None:
    init_trade_schema(conn)
    cur = conn.execute(
        "SELECT id, name, owner, currency, opening_cash FROM trade_funds WHERE name = ?",
        (name.strip(),),
    )
    row = cur.fetchone()
    if not row:
        return None
    return TradeFund(int(row[0]), row[1], row[2], row[3], float(row[4]))


def create_fund(
    conn: sqlite3.Connection,
    name: str,
    owner: str,
    opening_cash: float,
    currency: str = "USD",
) -> TradeFund:
    init_trade_schema(conn)
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO trade_funds (name, owner, currency, opening_cash, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name.strip(), owner.strip(), currency.strip().upper(), opening_cash, now),
    )
    conn.commit()
    fid = int(cur.lastrowid)
    return TradeFund(fid, name.strip(), owner.strip(), currency.strip().upper(), opening_cash)


def insert_trade(
    conn: sqlite3.Connection,
    fund_id: int,
    trade_date: date,
    ticker: str,
    side: str,
    shares: float,
    price: float,
    fees: float = 0.0,
    notes: str | None = None,
    *,
    instrument_type: str = "stock",
    underlying: str | None = None,
    expiry: date | None = None,
    strike: float | None = None,
    put_call: str | None = None,
    contract_multiplier: float | None = None,
) -> int:
    init_trade_schema(conn)
    side = side.strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    if shares <= 0 or price < 0:
        raise ValueError("shares must be > 0 and price >= 0")

    inst = instrument_type.strip().lower()
    if inst not in ("stock", "option"):
        raise ValueError("instrument_type must be 'stock' or 'option'")

    occ = ticker.strip().upper()
    und = underlying.strip().upper() if underlying else None
    exp = expiry
    stk = float(strike) if strike is not None else None
    pc = put_call.strip().lower() if put_call else None
    mult = float(contract_multiplier) if contract_multiplier is not None else 1.0

    if inst == "option":
        from ledger.option_symbols import resolve_option_trade

        occ, und, exp, stk, pc = resolve_option_trade(
            ticker=occ or und,
            expiry=exp,
            strike=stk,
            put_call=pc,
        )
        mult = 100.0 if contract_multiplier is None else float(contract_multiplier)
        if mult <= 0:
            raise ValueError("contract_multiplier must be > 0")
    else:
        if not occ:
            raise ValueError("ticker is required")
        und = exp = stk = pc = None
        mult = 1.0

    now = datetime.utcnow().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO trades (
            fund_id, trade_date, ticker, side, shares, price, fees, notes, created_at,
            instrument_type, underlying, expiry, strike, put_call, contract_multiplier
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fund_id,
            trade_date.isoformat(),
            occ,
            side,
            shares,
            price,
            fees,
            notes,
            now,
            inst,
            und,
            exp.isoformat() if exp else None,
            stk,
            pc,
            mult,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def delete_trades_for_fund_notes_like(
    conn: sqlite3.Connection,
    fund_name: str,
    notes_substring: str,
) -> int:
    """Delete trades whose notes contain substring (case-insensitive). Returns rows removed."""
    init_trade_schema(conn)
    fund = get_fund_by_name(conn, fund_name)
    if fund is None:
        return 0
    needle = notes_substring.strip().upper()
    if not needle:
        return 0
    cur = conn.execute(
        """
        DELETE FROM trades
        WHERE fund_id = ? AND UPPER(COALESCE(notes, '')) LIKE ?
        """,
        (fund.id, f"%{needle}%"),
    )
    conn.commit()
    return int(cur.rowcount)


def delete_fund(conn: sqlite3.Connection, fund_name: str) -> bool:
    """Remove a fund and all its trades and performance snapshots."""
    init_trade_schema(conn)
    fund = get_fund_by_name(conn, fund_name)
    if fund is None:
        return False
    conn.execute("DELETE FROM fund_performance_snapshots WHERE fund_id = ?", (fund.id,))
    conn.execute("DELETE FROM fund_manual_balances WHERE fund_id = ?", (fund.id,))
    conn.execute("DELETE FROM fund_cash_events WHERE fund_id = ?", (fund.id,))
    conn.execute("DELETE FROM fund_reported_cash WHERE fund_id = ?", (fund.id,))
    conn.execute("DELETE FROM fund_ticker_marks WHERE fund_id = ?", (fund.id,))
    conn.execute("DELETE FROM trades WHERE fund_id = ?", (fund.id,))
    conn.execute("DELETE FROM trade_funds WHERE id = ?", (fund.id,))
    conn.commit()
    return True


def fetch_trades_for_fund(conn: sqlite3.Connection, fund_id: int) -> list[TradeRecord]:
    init_trade_schema(conn)
    cur = conn.execute(
        """
        SELECT id, fund_id, trade_date, ticker, side, shares, price, fees, notes,
               instrument_type, underlying, expiry, strike, put_call, contract_multiplier
        FROM trades WHERE fund_id = ?
        ORDER BY trade_date ASC, id ASC
        """,
        (fund_id,),
    )
    return [_row_to_trade_record(r) for r in cur.fetchall()]


def delete_fund_performance_snapshots(conn: sqlite3.Connection, fund_id: int) -> int:
    init_trade_schema(conn)
    cur = conn.execute(
        "DELETE FROM fund_performance_snapshots WHERE fund_id = ?",
        (fund_id,),
    )
    conn.commit()
    return int(cur.rowcount)


def upsert_fund_performance_snapshot(
    conn: sqlite3.Connection,
    snapshot_date: date,
    fund_id: int,
    cash_balance: float,
    positions_value: float,
    nav: float,
    invested_capital: float,
    cumulative_realized_pl: float,
    roi_pct: float | None,
) -> None:
    init_trade_schema(conn)
    conn.execute(
        """
        INSERT INTO fund_performance_snapshots (
            snapshot_date, fund_id, cash_balance, positions_value, nav,
            invested_capital, cumulative_realized_pl, roi_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, fund_id) DO UPDATE SET
            cash_balance = excluded.cash_balance,
            positions_value = excluded.positions_value,
            nav = excluded.nav,
            invested_capital = excluded.invested_capital,
            cumulative_realized_pl = excluded.cumulative_realized_pl,
            roi_pct = excluded.roi_pct
        """,
        (
            snapshot_date.isoformat(),
            fund_id,
            cash_balance,
            positions_value,
            nav,
            invested_capital,
            cumulative_realized_pl,
            roi_pct,
        ),
    )
    conn.commit()


def delete_fund_cash_events(conn: sqlite3.Connection, fund_id: int) -> None:
    init_trade_schema(conn)
    conn.execute("DELETE FROM fund_cash_events WHERE fund_id = ?", (fund_id,))
    conn.commit()


def insert_fund_cash_event(
    conn: sqlite3.Connection,
    fund_id: int,
    event_date: date,
    amount: float,
    notes: str | None = None,
) -> int:
    init_trade_schema(conn)
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO fund_cash_events (fund_id, event_date, amount, notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (fund_id, event_date.isoformat(), amount, notes, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def fetch_fund_cash_events(conn: sqlite3.Connection, fund_id: int) -> list[FundCashEvent]:
    init_trade_schema(conn)
    cur = conn.execute(
        """
        SELECT id, fund_id, event_date, amount, notes
        FROM fund_cash_events
        WHERE fund_id = ?
        ORDER BY event_date ASC, id ASC
        """,
        (fund_id,),
    )
    return [
        FundCashEvent(
            id=int(r[0]),
            fund_id=int(r[1]),
            event_date=date.fromisoformat(r[2]),
            amount=float(r[3]),
            notes=r[4],
        )
        for r in cur.fetchall()
    ]


def upsert_fund_reported_cash(
    conn: sqlite3.Connection,
    fund_id: int,
    balance_date: date,
    cash_balance: float,
    *,
    source: str | None = None,
) -> None:
    init_trade_schema(conn)
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO fund_reported_cash (fund_id, balance_date, cash_balance, source, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(fund_id) DO UPDATE SET
            balance_date = excluded.balance_date,
            cash_balance = excluded.cash_balance,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (fund_id, balance_date.isoformat(), cash_balance, source, now),
    )
    conn.commit()


def fetch_fund_reported_cash(conn: sqlite3.Connection, fund_id: int) -> FundReportedCash | None:
    init_trade_schema(conn)
    cur = conn.execute(
        """
        SELECT fund_id, balance_date, cash_balance, source
        FROM fund_reported_cash WHERE fund_id = ?
        """,
        (fund_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return FundReportedCash(
        fund_id=int(row[0]),
        balance_date=date.fromisoformat(row[1]),
        cash_balance=float(row[2]),
        source=row[3],
    )


def delete_fund_ticker_marks(conn: sqlite3.Connection, fund_id: int) -> int:
    init_trade_schema(conn)
    cur = conn.execute("DELETE FROM fund_ticker_marks WHERE fund_id = ?", (fund_id,))
    conn.commit()
    return int(cur.rowcount)


def upsert_fund_ticker_mark(
    conn: sqlite3.Connection,
    fund_id: int,
    mark_date: date,
    ticker: str,
    price: float,
    *,
    currency: str = "GBP",
    source: str | None = None,
) -> None:
    init_trade_schema(conn)
    conn.execute(
        """
        INSERT INTO fund_ticker_marks (fund_id, mark_date, ticker, price, currency, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(fund_id, mark_date, ticker) DO UPDATE SET
            price = excluded.price,
            currency = excluded.currency,
            source = excluded.source
        """,
        (fund_id, mark_date.isoformat(), ticker.upper(), price, currency, source),
    )
    conn.commit()


def fetch_fund_ticker_marks(
    conn: sqlite3.Connection,
    fund_id: int,
) -> dict[str, tuple[tuple[date, ...], tuple[float, ...]]]:
    init_trade_schema(conn)
    cur = conn.execute(
        """
        SELECT mark_date, ticker, price
        FROM fund_ticker_marks
        WHERE fund_id = ?
        ORDER BY ticker, mark_date
        """,
        (fund_id,),
    )
    raw: dict[str, list[tuple[date, float]]] = {}
    for row in cur.fetchall():
        d = date.fromisoformat(str(row[0]))
        ticker = str(row[1]).upper()
        raw.setdefault(ticker, []).append((d, float(row[2])))
    return {
        ticker: (tuple(d for d, _ in rows), tuple(px for _, px in rows))
        for ticker, rows in raw.items()
    }


def fetch_fund_manual_balances(conn: sqlite3.Connection) -> dict[int, FundManualBalance]:
    init_trade_schema(conn)
    cur = conn.execute(
        """
        SELECT fund_id, balance_date, nav, cash_balance, positions_value, notes, updated_at
        FROM fund_manual_balances
        """
    )
    out: dict[int, FundManualBalance] = {}
    for r in cur.fetchall():
        fid = int(r[0])
        out[fid] = FundManualBalance(
            fund_id=fid,
            balance_date=date.fromisoformat(r[1]),
            nav=float(r[2]),
            cash_balance=float(r[3]) if r[3] is not None else None,
            positions_value=float(r[4]) if r[4] is not None else None,
            notes=r[5],
            updated_at=str(r[6]),
        )
    return out


def upsert_fund_manual_balance(
    conn: sqlite3.Connection,
    fund_id: int,
    balance_date: date,
    nav: float,
    *,
    cash_balance: float | None = None,
    positions_value: float | None = None,
    notes: str | None = None,
) -> None:
    init_trade_schema(conn)
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO fund_manual_balances (
            fund_id, balance_date, nav, cash_balance, positions_value, notes, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fund_id) DO UPDATE SET
            balance_date = excluded.balance_date,
            nav = excluded.nav,
            cash_balance = excluded.cash_balance,
            positions_value = excluded.positions_value,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (
            fund_id,
            balance_date.isoformat(),
            nav,
            cash_balance,
            positions_value,
            notes,
            now,
        ),
    )
    conn.commit()


def clear_fund_manual_balance(conn: sqlite3.Connection, fund_id: int) -> bool:
    init_trade_schema(conn)
    cur = conn.execute("DELETE FROM fund_manual_balances WHERE fund_id = ?", (fund_id,))
    conn.commit()
    return cur.rowcount > 0


def upsert_owner_other_asset(
    conn: sqlite3.Connection,
    owner: str,
    asset_type: str,
    amount: float,
    *,
    currency: str = "USD",
    notes: str | None = None,
) -> None:
    """Non-fund assets (bank accounts, property, ...) tracked as a single running total per owner+type."""
    init_trade_schema(conn)
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO owner_other_assets (owner, asset_type, amount, currency, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner, asset_type) DO UPDATE SET
            amount = excluded.amount,
            currency = excluded.currency,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (owner.strip(), asset_type.strip(), amount, currency.strip().upper(), notes, now),
    )
    conn.commit()


def fetch_owner_other_assets(
    conn: sqlite3.Connection, owner: str | None = None
) -> list[OwnerOtherAsset]:
    init_trade_schema(conn)
    if owner:
        cur = conn.execute(
            """
            SELECT owner, asset_type, amount, currency, notes, updated_at
            FROM owner_other_assets WHERE owner = ? ORDER BY asset_type
            """,
            (owner.strip(),),
        )
    else:
        cur = conn.execute(
            """
            SELECT owner, asset_type, amount, currency, notes, updated_at
            FROM owner_other_assets ORDER BY owner, asset_type
            """
        )
    return [
        OwnerOtherAsset(
            owner=str(row[0]),
            asset_type=str(row[1]),
            amount=float(row[2]),
            currency=str(row[3]),
            notes=row[4],
            updated_at=str(row[5]),
        )
        for row in cur.fetchall()
    ]


def delete_owner_other_asset(conn: sqlite3.Connection, owner: str, asset_type: str) -> bool:
    init_trade_schema(conn)
    cur = conn.execute(
        "DELETE FROM owner_other_assets WHERE owner = ? AND asset_type = ?",
        (owner.strip(), asset_type.strip()),
    )
    conn.commit()
    return cur.rowcount > 0


def fetch_fund_performance_history(conn: sqlite3.Connection, fund_id: int) -> list[dict[str, Any]]:
    init_trade_schema(conn)
    cur = conn.execute(
        """
        SELECT snapshot_date, cash_balance, positions_value, nav,
               invested_capital, cumulative_realized_pl, roi_pct
        FROM fund_performance_snapshots
        WHERE fund_id = ?
        ORDER BY snapshot_date ASC
        """,
        (fund_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def fetch_mark_prices_for_owner(conn: sqlite3.Connection, owner: str) -> dict[str, float]:
    """Latest stock snapshot prices for an owner (from sheet snapshots)."""
    cur = conn.execute(
        """
        SELECT ticker, last_price FROM stock_daily_snapshots
        WHERE owner = ? AND snapshot_date = (
            SELECT MAX(snapshot_date) FROM stock_daily_snapshots WHERE owner = ?
        )
        """,
        (owner, owner),
    )
    return {str(r[0]): float(r[1]) for r in cur.fetchall()}


def fetch_option_strike_alerted(conn: sqlite3.Connection) -> set[str]:
    """OCC symbols that already triggered a near-strike alert."""
    init_trade_schema(conn)
    cur = conn.execute("SELECT occ_symbol FROM option_strike_near_alerts")
    return {str(row[0]) for row in cur.fetchall()}


def mark_option_strike_alerted(
    conn: sqlite3.Connection,
    *,
    occ_symbol: str,
    underlying: str,
    strike: float,
    expiry: date,
    put_call: str | None,
    underlying_price: float,
    distance: float,
) -> bool:
    """Record a near-strike alert. Returns True if newly inserted."""
    init_trade_schema(conn)
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO option_strike_near_alerts (
            occ_symbol, underlying, strike, expiry, put_call,
            notified_at, underlying_price, distance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            occ_symbol.strip().upper(),
            underlying.strip().upper(),
            float(strike),
            expiry.isoformat(),
            put_call,
            now,
            float(underlying_price),
            float(distance),
        ),
    )
    conn.commit()
    return cur.rowcount > 0

