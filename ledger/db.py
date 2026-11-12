from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_daily_snapshots (
    snapshot_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    ticker TEXT NOT NULL,
    shares REAL NOT NULL,
    avg_cost REAL NOT NULL,
    last_price REAL NOT NULL,
    cost_basis REAL NOT NULL,
    mkt_value REAL NOT NULL,
    pl_dollar REAL NOT NULL,
    pl_pct REAL NOT NULL,
    PRIMARY KEY (snapshot_date, owner, ticker)
);

CREATE TABLE IF NOT EXISTS fund_weekly_snapshots (
    week_start TEXT NOT NULL,
    owner TEXT NOT NULL,
    fund_name TEXT NOT NULL,
    cash_invested REAL,
    current_cash_balance REAL,
    active_purchase_amount REAL,
    fund_total_usd REAL,
    total_profit_usd REAL,
    PRIMARY KEY (week_start, owner, fund_name)
);

CREATE INDEX IF NOT EXISTS idx_stock_daily_date_owner
ON stock_daily_snapshots (snapshot_date, owner);

CREATE INDEX IF NOT EXISTS idx_fund_week_start_owner
ON fund_weekly_snapshots (week_start, owner);
"""


def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in cur.fetchall()}


def _migrate_fund_weekly_table(conn: sqlite3.Connection) -> None:
    """Upgrade legacy (week_start, owner, amount_i, amount_k) schema to per-fund rows."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "fund_weekly_snapshots" not in tables:
        return

    cols = _table_columns(conn, "fund_weekly_snapshots")
    if "fund_name" in cols:
        return

    conn.executescript(
        """
        ALTER TABLE fund_weekly_snapshots RENAME TO fund_weekly_snapshots_legacy;
        CREATE TABLE fund_weekly_snapshots (
            week_start TEXT NOT NULL,
            owner TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            cash_invested REAL,
            current_cash_balance REAL,
            active_purchase_amount REAL,
            fund_total_usd REAL,
            total_profit_usd REAL,
            PRIMARY KEY (week_start, owner, fund_name)
        );
        INSERT INTO fund_weekly_snapshots (
            week_start, owner, fund_name, fund_total_usd, total_profit_usd
        )
        SELECT week_start, owner, '(summary)', amount_i, amount_k
        FROM fund_weekly_snapshots_legacy;
        DROP TABLE fund_weekly_snapshots_legacy;
        CREATE INDEX IF NOT EXISTS idx_fund_week_start_owner
        ON fund_weekly_snapshots (week_start, owner);
        """
    )
    conn.commit()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()
    _migrate_fund_weekly_table(conn)


@dataclass(frozen=True)
class StockSnapshotRow:
    snapshot_date: date
    owner: str
    ticker: str
    shares: float
    avg_cost: float
    last_price: float
    cost_basis: float
    mkt_value: float
    pl_dollar: float
    pl_pct: float


def upsert_stock_daily_snapshots(conn: sqlite3.Connection, rows: Sequence[StockSnapshotRow]) -> int:
    init_schema(conn)
    cur = conn.cursor()
    n = 0
    for r in rows:
        cur.execute(
            """
            INSERT INTO stock_daily_snapshots (
                snapshot_date, owner, ticker, shares, avg_cost, last_price,
                cost_basis, mkt_value, pl_dollar, pl_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date, owner, ticker) DO UPDATE SET
                shares = excluded.shares,
                avg_cost = excluded.avg_cost,
                last_price = excluded.last_price,
                cost_basis = excluded.cost_basis,
                mkt_value = excluded.mkt_value,
                pl_dollar = excluded.pl_dollar,
                pl_pct = excluded.pl_pct
            """,
            (
                r.snapshot_date.isoformat(),
                r.owner,
                r.ticker,
                r.shares,
                r.avg_cost,
                r.last_price,
                r.cost_basis,
                r.mkt_value,
                r.pl_dollar,
                r.pl_pct,
            ),
        )
        n += 1
    conn.commit()
    return n


@dataclass(frozen=True)
class FundWeeklyRow:
    week_start: date
    owner: str
    fund_name: str
    cash_invested: float | None = None
    current_cash_balance: float | None = None
    active_purchase_amount: float | None = None
    fund_total_usd: float | None = None
    total_profit_usd: float | None = None

    @property
    def amount_i(self) -> float | None:
        return self.fund_total_usd

    @property
    def amount_k(self) -> float | None:
        return self.total_profit_usd


def delete_fund_weekly_for_week_owner(conn: sqlite3.Connection, week_start: date, owner: str) -> None:
    init_schema(conn)
    conn.execute(
        "DELETE FROM fund_weekly_snapshots WHERE week_start = ? AND owner = ?",
        (week_start.isoformat(), owner),
    )
    conn.commit()


def upsert_fund_weekly_snapshot(conn: sqlite3.Connection, row: FundWeeklyRow) -> None:
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO fund_weekly_snapshots (
            week_start, owner, fund_name,
            cash_invested, current_cash_balance, active_purchase_amount,
            fund_total_usd, total_profit_usd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(week_start, owner, fund_name) DO UPDATE SET
            cash_invested = excluded.cash_invested,
            current_cash_balance = excluded.current_cash_balance,
            active_purchase_amount = excluded.active_purchase_amount,
            fund_total_usd = excluded.fund_total_usd,
            total_profit_usd = excluded.total_profit_usd
        """,
        (
            row.week_start.isoformat(),
            row.owner,
            row.fund_name,
            row.cash_invested,
            row.current_cash_balance,
            row.active_purchase_amount,
            row.fund_total_usd,
            row.total_profit_usd,
        ),
    )
    conn.commit()


def replace_fund_weekly_snapshots(
    conn: sqlite3.Connection, week_start: date, owner: str, rows: Sequence[FundWeeklyRow]
) -> int:
    """Replace all fund lines for one owner/week (used after reading Fund List)."""
    delete_fund_weekly_for_week_owner(conn, week_start, owner)
    for r in rows:
        upsert_fund_weekly_snapshot(conn, r)
    return len(rows)


def fetch_stock_totals_by_owner(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    init_schema(conn)
    cur = conn.execute(
        """
        SELECT snapshot_date, owner, SUM(mkt_value) AS total_mkt_value
        FROM stock_daily_snapshots
        GROUP BY snapshot_date, owner
        ORDER BY snapshot_date ASC, owner ASC
        """
    )
    return [dict(r) for r in cur.fetchall()]


def fetch_fund_weekly(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    init_schema(conn)
    cur = conn.execute(
        """
        SELECT week_start, owner, fund_name,
               cash_invested, current_cash_balance, active_purchase_amount,
               fund_total_usd, total_profit_usd
        FROM fund_weekly_snapshots
        ORDER BY week_start ASC, owner ASC, fund_name ASC
        """
    )
    return [dict(r) for r in cur.fetchall()]


def fetch_fund_owner_weekly_totals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Sum fund lines per owner/week for trend charts."""
    init_schema(conn)
    cur = conn.execute(
        """
        SELECT week_start, owner,
               SUM(fund_total_usd) AS fund_total_usd,
               SUM(total_profit_usd) AS total_profit_usd
        FROM fund_weekly_snapshots
        GROUP BY week_start, owner
        ORDER BY week_start ASC, owner ASC
        """
    )
    return [dict(r) for r in cur.fetchall()]


def fetch_latest_stock_holdings(conn: sqlite3.Connection, owner: str) -> list[dict[str, Any]]:
    """Positions for the most recent snapshot_date for this owner."""
    init_schema(conn)
    cur = conn.execute(
        """
        SELECT snapshot_date FROM stock_daily_snapshots
        WHERE owner = ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        (owner,),
    )
    row = cur.fetchone()
    if not row:
        return []
    sd = row["snapshot_date"]
    cur = conn.execute(
        """
        SELECT ticker, mkt_value, pl_dollar, pl_pct, cost_basis
        FROM stock_daily_snapshots
        WHERE owner = ? AND snapshot_date = ?
        ORDER BY mkt_value DESC
        """,
        (owner, sd),
    )
    return [dict(r) for r in cur.fetchall()]


def fetch_latest_fund_lines(conn: sqlite3.Connection, owner: str) -> list[dict[str, Any]]:
    """All fund rows for the most recent week_start for this owner."""
    init_schema(conn)
    cur = conn.execute(
        """
        SELECT week_start FROM fund_weekly_snapshots
        WHERE owner = ?
        ORDER BY week_start DESC
        LIMIT 1
        """,
        (owner,),
    )
    row = cur.fetchone()
    if not row:
        return []
    ws = row["week_start"]
    cur = conn.execute(
        """
        SELECT fund_name, cash_invested, current_cash_balance, active_purchase_amount,
               fund_total_usd, total_profit_usd
        FROM fund_weekly_snapshots
        WHERE owner = ? AND week_start = ?
        ORDER BY fund_name ASC
        """,
        (owner, ws),
    )
    return [dict(r) for r in cur.fetchall()]


def fetch_latest_two_fund_weekly(
    conn: sqlite3.Connection, owner: str
) -> tuple[tuple[float | None, float | None], tuple[float | None, float | None]]:
    """Owner-level summed (fund total USD, total profit USD) for latest two weeks."""
    init_schema(conn)
    cur = conn.execute(
        """
        SELECT week_start,
               SUM(fund_total_usd) AS fund_total_usd,
               SUM(total_profit_usd) AS total_profit_usd
        FROM fund_weekly_snapshots
        WHERE owner = ?
        GROUP BY week_start
        ORDER BY week_start DESC
        LIMIT 2
        """,
        (owner,),
    )
    rows = cur.fetchall()
    if not rows:
        return (None, None), (None, None)
    i0 = rows[0]["fund_total_usd"]
    k0 = rows[0]["total_profit_usd"]
    if len(rows) < 2:
        return (
            (float(i0) if i0 is not None else None, None),
            (float(k0) if k0 is not None else None, None),
        )
    i1 = rows[1]["fund_total_usd"]
    k1 = rows[1]["total_profit_usd"]
    return (
        (float(i0) if i0 is not None else None, float(i1) if i1 is not None else None),
        (float(k0) if k0 is not None else None, float(k1) if k1 is not None else None),
    )


def fetch_latest_two_stock_totals(
    conn: sqlite3.Connection, owner: str
) -> tuple[float | None, float | None]:
    init_schema(conn)
    cur = conn.execute(
        """
        SELECT snapshot_date, SUM(mkt_value) AS total_mkt_value
        FROM stock_daily_snapshots
        WHERE owner = ?
        GROUP BY snapshot_date
        ORDER BY snapshot_date DESC
        LIMIT 2
        """,
        (owner,),
    )
    rows = cur.fetchall()
    if not rows:
        return None, None
    latest = float(rows[0]["total_mkt_value"])
    prior = float(rows[1]["total_mkt_value"]) if len(rows) > 1 else None
    return latest, prior
