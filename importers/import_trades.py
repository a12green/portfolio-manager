from __future__ import annotations

from config import OWNER_A
import csv
import sqlite3
from datetime import date, datetime
from pathlib import Path

from ledger.trades_db import create_fund, get_fund_by_name, insert_trade, init_trade_schema


def _parse_date(raw: str) -> date:
    s = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not parse date: {raw!r}")


def import_trades_csv(conn: sqlite3.Connection, path: Path) -> int:
    """
    CSV columns: trade_date, fund, ticker, side, shares, price
    Optional: fees, notes, owner, currency, opening_cash (for auto-create fund)
    Options: instrument_type, underlying, expiry, strike, put_call, contract_multiplier
    """
    init_trade_schema(conn)
    path = path.expanduser()
    n = 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        fields = {h.strip().lower(): h for h in reader.fieldnames if h}
        need = {"trade_date", "fund", "ticker", "side", "shares", "price"}
        if not need.issubset(fields.keys()):
            raise ValueError(f"CSV needs columns {sorted(need)}; got {list(reader.fieldnames)}")

        for i, row in enumerate(reader, start=2):
            fund_name = str(row.get(fields["fund"], "")).strip()
            if not fund_name:
                continue
            fund = get_fund_by_name(conn, fund_name)
            if fund is None:
                owner = str(row.get(fields.get("owner", ""), "") or OWNER_A).strip()
                currency = str(row.get(fields.get("currency", ""), "") or "USD").strip().upper()
                opening = float(row.get(fields.get("opening_cash", ""), "") or 0)
                fund = create_fund(conn, fund_name, owner, opening, currency)

            fees = float(row.get(fields.get("fees", ""), "") or 0)
            notes = row.get(fields.get("notes", ""), None)
            inst = str(row.get(fields.get("instrument_type", ""), "") or "stock").strip().lower()
            underlying = str(row.get(fields.get("underlying", ""), "") or "").strip() or None
            ticker_val = str(row[fields["ticker"]])
            if inst == "option" and underlying and not ticker_val.strip():
                ticker_val = underlying
            expiry_raw = str(row.get(fields.get("expiry", ""), "") or "").strip()
            expiry = _parse_date(expiry_raw) if expiry_raw else None
            strike_raw = str(row.get(fields.get("strike", ""), "") or "").strip()
            strike = float(strike_raw) if strike_raw else None
            put_call = str(row.get(fields.get("put_call", ""), "") or "").strip().lower() or None
            mult_raw = str(row.get(fields.get("contract_multiplier", ""), "") or "").strip()
            multiplier = float(mult_raw) if mult_raw else None
            insert_trade(
                conn,
                fund.id,
                _parse_date(str(row[fields["trade_date"]])),
                ticker_val,
                str(row[fields["side"]]),
                float(row[fields["shares"]]),
                float(row[fields["price"]]),
                fees,
                str(notes).strip() if notes else None,
                instrument_type=inst,
                expiry=expiry,
                strike=strike,
                put_call=put_call,
                contract_multiplier=multiplier,
            )
            n += 1
    return n
