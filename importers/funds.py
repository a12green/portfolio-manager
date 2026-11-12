from __future__ import annotations

from config import OWNER_A, OWNER_B, OWNER_C
import math
import os
import re
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from ledger.db import FundWeeklyRow
from ledger.money import parse_money_scalar

# Indices within an A:K row (A=0 … K=10).
_COL_A, _COL_B, _COL_D, _COL_E, _COL_I, _COL_K = 0, 1, 3, 4, 8, 10


def _quoted_sheet_title(title: str) -> str:
    if "'" in title:
        raise ValueError("Worksheet titles containing apostrophes are not supported for range quoting.")
    return f"'{title}'"


def _service_account_credentials() -> Credentials:
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service-account.json")
    sa_path = Path(service_account_file).expanduser()
    if not sa_path.is_file():
        raise FileNotFoundError(
            f"Service account key not found at {sa_path.resolve()}. "
            "Set GOOGLE_SERVICE_ACCOUNT_FILE or place service-account.json in the project."
        )
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    return Credentials.from_service_account_file(str(sa_path), scopes=scopes)


def _parse_row_range(spec: str) -> tuple[int, int]:
    spec = spec.strip()
    m = re.fullmatch(r"(\d+)\s*:\s*(\d+)", spec)
    if not m:
        raise ValueError(f"OWNER_A_FUND_ROWS must look like '2:9'; got {spec!r}")
    a, b = int(m.group(1)), int(m.group(2))
    if a <= 0 or b <= 0:
        raise ValueError(f"Invalid row range {spec!r}")
    start, end = (a, b) if a <= b else (b, a)
    return start, end


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


def _cell_str(row: list[Any], idx: int) -> str:
    if idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def _cell_money(row: list[Any], idx: int) -> float | None:
    v = parse_money_scalar(_cell_str(row, idx) if idx < len(row) else "")
    return None if math.isnan(v) else float(v)


def _parse_fund_row(row: list[Any], owner: str, week_start: Any) -> FundWeeklyRow | None:
    fund_name = _cell_str(row, _COL_A)
    cash_b = _cell_money(row, _COL_B)
    cash_d = _cell_money(row, _COL_D)
    active_e = _cell_money(row, _COL_E)
    total_i = _cell_money(row, _COL_I)
    profit_k = _cell_money(row, _COL_K)

    if not fund_name:
        fund_name = owner

    if all(v is None for v in (cash_b, cash_d, active_e, total_i, profit_k)):
        return None

    from datetime import date

    if not isinstance(week_start, date):
        raise TypeError("week_start must be a date")

    return FundWeeklyRow(
        week_start=week_start,
        owner=owner,
        fund_name=fund_name,
        cash_invested=cash_b,
        current_cash_balance=cash_d,
        active_purchase_amount=active_e,
        fund_total_usd=total_i,
        total_profit_usd=profit_k,
    )


def _parse_block(grid: list[list[Any]] | None, owner: str, week_start: Any) -> list[FundWeeklyRow]:
    if not grid:
        return []
    out: list[FundWeeklyRow] = []
    for row in grid:
        parsed = _parse_fund_row(row, owner, week_start)
        if parsed is not None:
            out.append(parsed)
    return out


def load_fund_lines_from_sheet(week_start: Any) -> dict[str, list[FundWeeklyRow]]:
    """
    Read Fund List columns A,B,D,E,I,K per fund row.

    OWNER_A: rows OWNER_A_FUND_ROWS (default 2:9), one fund per row.
    OWNER_B / OWNER_C: single rows OWNER_B_FUND_ROW / OWNER_C_FUND_ROW (default 10 / 11).
    """
    from datetime import date

    if not isinstance(week_start, date):
        raise TypeError("week_start must be a date")

    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID is required for fund snapshots.")

    ws_title = os.getenv("GOOGLE_FUND_LIST_SHEET_NAME", "Fund List").strip()
    owner_a_rows = os.getenv("OWNER_A_FUND_ROWS", "2:9").strip()
    owner_b_row = _parse_int_env("OWNER_B_FUND_ROW", 10)
    owner_c_row = _parse_int_env("OWNER_C_FUND_ROW", 11)
    a_start, a_end = _parse_row_range(owner_a_rows)
    q = _quoted_sheet_title(ws_title)

    ranges = [
        f"{q}!A{a_start}:K{a_end}",
        f"{q}!A{owner_b_row}:K{owner_b_row}",
        f"{q}!A{owner_c_row}:K{owner_c_row}",
    ]

    credentials = _service_account_credentials()
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(spreadsheet_id)

    resp = spreadsheet.values_batch_get(ranges)
    if not isinstance(resp, dict):
        raise TypeError(f"Unexpected batch_get response type: {type(resp)!r}")

    grids: list[list[list[Any]] | None] = []
    for vr in resp.get("valueRanges", []):
        grids.append(vr.get("values"))

    if len(grids) != 3:
        raise RuntimeError(f"Expected 3 ranges from batch_get; got {len(grids)}")

    return {
        OWNER_A: _parse_block(grids[0], OWNER_A, week_start),
        OWNER_B: _parse_block(grids[1], OWNER_B, week_start),
        OWNER_C: _parse_block(grids[2], OWNER_C, week_start),
    }


def load_fund_owner_amounts_from_sheet() -> dict[str, tuple[float | None, float | None]]:
    """Legacy helper: summed I/K per owner from current sheet (no week_start in rows)."""
    from datetime import date

    lines = load_fund_lines_from_sheet(date.today())

    def _sum(owner: str, attr: str) -> float | None:
        vals = [getattr(r, attr) for r in lines[owner] if getattr(r, attr) is not None]
        return sum(vals) if vals else None

    return {
        owner: (_sum(owner, "fund_total_usd"), _sum(owner, "total_profit_usd"))
        for owner in (OWNER_A, OWNER_B, OWNER_C)
    }
