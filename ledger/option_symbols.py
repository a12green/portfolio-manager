"""US equity option symbols — OCC format parsing, building, and display labels."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

_OCC_RE = re.compile(
    r"^([A-Z]{1,6})\s*(\d{6})([CP])(\d{8})$",
    re.IGNORECASE,
)
# Fidelity activity exports: SEZL260717C190 or -OSS260717C30 / -INTT260717C22.5
_FIDELITY_OPT_RE = re.compile(
    r"^-?([A-Z]{1,6})(\d{6})([CP])(\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    expiry: date
    put_call: str  # "call" or "put"
    strike: float
    occ_symbol: str

    @property
    def is_call(self) -> bool:
        return self.put_call == "call"

    @property
    def multiplier(self) -> float:
        return 100.0


def is_occ_symbol(ticker: str) -> bool:
    return parse_occ_symbol(ticker) is not None


def parse_occ_symbol(ticker: str) -> OptionContract | None:
    """Parse OCC or Fidelity option symbols (e.g. AAPL260618C00150000, -SEZL260717C190)."""
    raw = str(ticker or "").strip().upper().replace(" ", "")
    m = _OCC_RE.match(raw)
    if m:
        root, yymmdd, cp, strike_raw = m.groups()
        try:
            expiry = datetime.strptime(yymmdd, "%y%m%d").date()
        except ValueError:
            return None
        strike = int(strike_raw) / 1000.0
        put_call = "call" if cp.upper() == "C" else "put"
        occ = build_occ_symbol(root, expiry, put_call, strike)
        return OptionContract(
            underlying=root.strip(),
            expiry=expiry,
            put_call=put_call,
            strike=strike,
            occ_symbol=occ,
        )
    m = _FIDELITY_OPT_RE.match(raw)
    if not m:
        return None
    root, yymmdd, cp, strike_raw = m.groups()
    try:
        expiry = datetime.strptime(yymmdd, "%y%m%d").date()
        strike = float(strike_raw)
    except ValueError:
        return None
    if strike <= 0:
        return None
    put_call = "call" if cp.upper() == "C" else "put"
    occ = build_occ_symbol(root, expiry, put_call, strike)
    return OptionContract(
        underlying=root.strip(),
        expiry=expiry,
        put_call=put_call,
        strike=strike,
        occ_symbol=occ,
    )


def build_occ_symbol(
    underlying: str,
    expiry: date,
    put_call: str,
    strike: float,
) -> str:
    root = str(underlying or "").strip().upper()
    if not root:
        raise ValueError("underlying is required for an option")
    pc = str(put_call or "").strip().lower()
    if pc not in ("call", "put", "c", "p"):
        raise ValueError("put_call must be call or put")
    cp = "C" if pc.startswith("c") else "P"
    strike_int = int(round(float(strike) * 1000))
    if strike_int <= 0:
        raise ValueError("strike must be > 0")
    return f"{root}{expiry.strftime('%y%m%d')}{cp}{strike_int:08d}"


def display_option_label(
    *,
    underlying: str | None = None,
    expiry: date | None = None,
    strike: float | None = None,
    put_call: str | None = None,
    occ_symbol: str | None = None,
) -> str:
    """Human-readable option label for tables."""
    if occ_symbol:
        parsed = parse_occ_symbol(occ_symbol)
        if parsed:
            return display_option_label(
                underlying=parsed.underlying,
                expiry=parsed.expiry,
                strike=parsed.strike,
                put_call=parsed.put_call,
            )
        return occ_symbol
    if not underlying or expiry is None or strike is None or not put_call:
        return occ_symbol or underlying or "Option"
    cp = "C" if str(put_call).lower().startswith("c") else "P"
    strike_s = f"{strike:g}"
    return f"{underlying.upper()} ${strike_s}{cp} {expiry:%m/%d/%y}"


def resolve_option_trade(
    *,
    ticker: str | None = None,
    underlying: str | None = None,
    expiry: date | None = None,
    strike: float | None = None,
    put_call: str | None = None,
) -> tuple[str, str, date, float, str]:
    """
    Resolve option trade fields to (occ_symbol, underlying, expiry, strike, put_call).
    Accepts a full OCC ticker, or ticker (underlying symbol) plus expiry, strike, and call/put.
    """
    raw = (ticker or underlying or "").strip().upper()
    if raw:
        parsed = parse_occ_symbol(raw)
        if parsed:
            return (
                parsed.occ_symbol,
                parsed.underlying,
                parsed.expiry,
                parsed.strike,
                parsed.put_call,
            )
    if not raw or expiry is None or strike is None or not put_call:
        raise ValueError(
            "Options require a ticker (OCC symbol or underlying) plus expiry, strike, and call/put"
        )
    occ = build_occ_symbol(raw, expiry, put_call, strike)
    pc = "call" if str(put_call).lower().startswith("c") else "put"
    return occ, raw, expiry, float(strike), pc
