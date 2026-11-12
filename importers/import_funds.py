from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
import csv
import math
import sqlite3
from datetime import date, datetime
from pathlib import Path

from ledger.db import FundWeeklyRow, connect_db, init_schema, upsert_fund_weekly_snapshot
from ledger.money import parse_money_scalar

_VALID_OWNERS = frozenset({OWNER_A, OWNER_B, OWNER_C})
_OWNER_ALIASES = {owner.lower(): owner for owner in (OWNER_A, OWNER_B, OWNER_C)}

# CSV column aliases → FundWeeklyRow field
_FIELD_ALIASES: dict[str, str] = {
    "week_start": "week_start",
    "owner": "owner",
    "fund_name": "fund_name",
    "fund": "fund_name",
    "name": "fund_name",
    "cash_invested": "cash_invested",
    "cash invested": "cash_invested",
    "b": "cash_invested",
    "current_cash_balance": "current_cash_balance",
    "cash balance": "current_cash_balance",
    "d": "current_cash_balance",
    "active_purchase_amount": "active_purchase_amount",
    "active purchases": "active_purchase_amount",
    "e": "active_purchase_amount",
    "fund_total_usd": "fund_total_usd",
    "amount_i": "fund_total_usd",
    "i": "fund_total_usd",
    "total_profit_usd": "total_profit_usd",
    "amount_k": "total_profit_usd",
    "k": "total_profit_usd",
}


def _parse_week_start(raw: str) -> date:
    s = raw.strip()
    if not s:
        raise ValueError("week_start is empty")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not parse week_start date: {raw!r} (use YYYY-MM-DD)")


def _normalize_owner(raw: str) -> str:
    key = raw.strip().lower()
    if key in _OWNER_ALIASES:
        return _OWNER_ALIASES[key]
    if raw.strip() in _VALID_OWNERS:
        return raw.strip()
    raise ValueError(f"Unknown owner {raw!r}; use one of: {', '.join(sorted(_VALID_OWNERS))}")


def _parse_amount(raw: str | None) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    v = parse_money_scalar(raw)
    return None if math.isnan(v) else float(v)


def _map_csv_fields(fieldnames: list[str] | None) -> dict[str, str]:
    """Map normalized header → original header key in CSV."""
    out: dict[str, str] = {}
    for h in fieldnames or []:
        key = h.strip().lower()
        canonical = _FIELD_ALIASES.get(key)
        if canonical:
            out[canonical] = h
    return out


def _row_from_csv_dict(row: dict[str, str], fields: dict[str, str], path: Path, line_no: int) -> FundWeeklyRow:
    ws = row.get(fields.get("week_start", ""), "")
    if not str(ws).strip():
        raise ValueError("week_start is empty")

    week_start = _parse_week_start(str(ws))
    owner = _normalize_owner(str(row.get(fields.get("owner", ""), "")))
    fund_name = str(row.get(fields.get("fund_name", ""), "") or "").strip() or owner

    def _amt(key: str) -> float | None:
        if key not in fields:
            return None
        return _parse_amount(row.get(fields[key]))

    return FundWeeklyRow(
        week_start=week_start,
        owner=owner,
        fund_name=fund_name,
        cash_invested=_amt("cash_invested"),
        current_cash_balance=_amt("current_cash_balance"),
        active_purchase_amount=_amt("active_purchase_amount"),
        fund_total_usd=_amt("fund_total_usd"),
        total_profit_usd=_amt("total_profit_usd"),
    )


def _read_long_format(path: Path) -> list[FundWeeklyRow]:
    rows: list[FundWeeklyRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        fields = _map_csv_fields(reader.fieldnames)
        need = {"week_start", "owner"}
        if not need.issubset(fields.keys()):
            raise ValueError(
                f"CSV needs at least week_start and owner. Got headers: {list(reader.fieldnames)}"
            )
        if "fund_total_usd" not in fields and "total_profit_usd" not in fields:
            raise ValueError("CSV needs fund_total_usd/amount_i and/or total_profit_usd/amount_k columns.")
        for i, row in enumerate(reader, start=2):
            if not any(str(v).strip() for v in row.values()):
                continue
            try:
                parsed = _row_from_csv_dict(row, fields, path, i)
            except ValueError as e:
                raise ValueError(f"{path.name} line {i}: {e}") from e
            rows.append(parsed)
    return rows


def _read_wide_format(path: Path) -> list[FundWeeklyRow]:
    """week_start + per-owner suffixed columns (e.g. owner_a_i, owner_a_b, …)."""
    rows: list[FundWeeklyRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        raw_fields = {h.strip().lower(): h for h in reader.fieldnames if h}
        if "week_start" not in raw_fields:
            raise ValueError("Wide-format CSV needs a week_start column")

        suffix_map = {
            OWNER_A: "owner_a",
            OWNER_B: "owner_b",
            OWNER_C: "owner_c",
        }
        col_suffix = {
            "fund_name": "_fund",
            "cash_invested": "_b",
            "current_cash_balance": "_d",
            "active_purchase_amount": "_e",
            "fund_total_usd": "_i",
            "total_profit_usd": "_k",
        }

        for i, row in enumerate(reader, start=2):
            ws = row.get(raw_fields["week_start"], "")
            if not str(ws).strip():
                continue
            week_start = _parse_week_start(str(ws))
            for owner, prefix in suffix_map.items():
                vals: dict[str, float | None] = {}
                for field, suf in col_suffix.items():
                    key = f"{prefix}{suf}"
                    if key in raw_fields:
                        vals[field] = _parse_amount(row.get(raw_fields[key]))
                    elif field == "fund_total_usd" and f"{prefix}_i" in raw_fields:
                        vals[field] = _parse_amount(row.get(raw_fields[f"{prefix}_i"]))
                    elif field == "total_profit_usd" and f"{prefix}_k" in raw_fields:
                        vals[field] = _parse_amount(row.get(raw_fields[f"{prefix}_k"]))

                if not any(v is not None for v in vals.values()):
                    continue
                fund_key = f"{prefix}_fund"
                fund_name = (
                    str(row.get(raw_fields[fund_key], "") or "").strip()
                    if fund_key in raw_fields
                    else owner
                )
                rows.append(
                    FundWeeklyRow(
                        week_start=week_start,
                        owner=owner,
                        fund_name=fund_name or owner,
                        cash_invested=vals.get("cash_invested"),
                        current_cash_balance=vals.get("current_cash_balance"),
                        active_purchase_amount=vals.get("active_purchase_amount"),
                        fund_total_usd=vals.get("fund_total_usd"),
                        total_profit_usd=vals.get("total_profit_usd"),
                    )
                )
    return rows


def load_fund_rows_from_csv(path: Path) -> list[FundWeeklyRow]:
    path = path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path.resolve()}")

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = {h.strip().lower() for h in (reader.fieldnames or []) if h}

    if "owner" in fields:
        rows = _read_long_format(path)
    elif "owner_a_i" in fields or "owner_a_k" in fields:
        rows = _read_wide_format(path)
    else:
        raise ValueError(
            "Unrecognized CSV layout. Use long format (week_start, owner, fund_name, …) "
            "or wide format (week_start, owner_a_i, owner_a_k, …)."
        )

    if not rows:
        raise ValueError(f"No data rows in {path.name}")
    return rows


def import_fund_history_csv(conn: sqlite3.Connection, path: Path) -> int:
    rows = load_fund_rows_from_csv(path)
    init_schema(conn)
    for r in rows:
        upsert_fund_weekly_snapshot(conn, r)
    return len(rows)
