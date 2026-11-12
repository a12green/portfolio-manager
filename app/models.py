"""Plain result types returned by the shared application layer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LedgerPosition:
    ticker: str
    display_name: str
    instrument_type: str
    shares: float
    avg_cost: float
    mark_price: float | None
    market_value: float | None
    unrealized_pl: float | None
    realized_pl: float | None
    currency: str


@dataclass(frozen=True)
class FundSummary:
    name: str
    currency: str
    cash: float
    nav: float
    roi_pct: float | None


@dataclass(frozen=True)
class LedgerDetail:
    positions: list[LedgerPosition] = field(default_factory=list)
    funds: list[FundSummary] = field(default_factory=list)
    fund_filter: str | None = None
