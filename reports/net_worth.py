"""Owner-level net worth: trade funds (manual balance if set, else live ledger) + other assets."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from ledger.quotes import fx_to_usd_rates, marks_for_fund
from ledger.trade_ledger import build_fund_ledger_with_db
from ledger.trades_db import (
    TradeFund,
    fetch_fund_manual_balances,
    fetch_owner_other_assets,
    init_trade_schema,
    list_funds,
)


@dataclass(frozen=True)
class NetWorthLine:
    label: str
    kind: str  # "fund" or "other"
    currency: str
    amount_local: float
    amount_usd: float
    as_of: str
    source: str  # "manual balance", "live ledger", "manual entry"


@dataclass(frozen=True)
class OwnerNetWorth:
    owner: str
    lines: tuple[NetWorthLine, ...]
    total_usd: float
    fx_rates: dict[str, float]


def _fund_total_local(
    conn: sqlite3.Connection,
    fund: TradeFund,
    manual_by_id: dict,
    *,
    use_live: bool,
) -> tuple[float, str, str]:
    """Manual balance overrides the ledger when set (same convention as fund_reported_cash)."""
    m = manual_by_id.get(fund.id)
    if m is not None:
        return m.nav, m.balance_date.isoformat(), "manual balance"
    marks = marks_for_fund(conn, fund, date.today(), live=use_live, force_live=use_live)
    result = build_fund_ledger_with_db(conn, fund, marks, date.today(), strict=False)
    return result.nav, date.today().isoformat(), "live ledger"


def compute_owner_net_worth(
    conn: sqlite3.Connection, owner: str, *, use_live: bool = False
) -> OwnerNetWorth:
    init_trade_schema(conn)
    owner = owner.strip()
    fx = fx_to_usd_rates(allow_network=use_live)
    manual_by_id = fetch_fund_manual_balances(conn)
    funds = [f for f in list_funds(conn) if f.owner.strip() == owner]

    lines: list[NetWorthLine] = []
    total = 0.0
    for f in funds:
        nav_local, as_of, source = _fund_total_local(conn, f, manual_by_id, use_live=use_live)
        rate = fx.get(f.currency.strip().upper(), 1.0)
        usd = nav_local * rate
        total += usd
        lines.append(NetWorthLine(f.name, "fund", f.currency, nav_local, usd, as_of, source))

    for asset in fetch_owner_other_assets(conn, owner):
        rate = fx.get(asset.currency.strip().upper(), 1.0)
        usd = asset.amount * rate
        total += usd
        lines.append(
            NetWorthLine(
                asset.asset_type,
                "other",
                asset.currency,
                asset.amount,
                usd,
                asset.updated_at[:10],
                "manual entry",
            )
        )

    lines.sort(key=lambda ln: -ln.amount_usd)
    return OwnerNetWorth(owner=owner, lines=tuple(lines), total_usd=total, fx_rates=fx)
