from __future__ import annotations

from config import OWNER_A, OWNER_B
import csv
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ledger.money import parse_money_scalar
from ledger.trades_db import (
    create_fund,
    delete_fund_cash_events,
    delete_trades_for_fund_notes_like,
    get_fund_by_name,
    init_trade_schema,
    insert_fund_cash_event,
    insert_trade,
    upsert_fund_reported_cash,
)

_FIDELITY_HSA_FUND = "Fidelity HSA"
_FIDELITY_SG_IRA_FUND = "Fidelity SG IRA"

# Fidelity export account name -> (trade fund name in this app, owner)
DEFAULT_ACCOUNT_MAP: dict[str, tuple[str, str]] = {
    "Limited Liability Company": ("Fidelity", OWNER_A),
    "Individual - TOD": ("Fidelity", OWNER_A),  # pre-LLC account; merged into Fidelity for history
    "Traditional IRA": ("Fidelity SG IRA", OWNER_A),
    "Health Savings Account": ("Fidelity HSA", OWNER_A),
}

_FIDELITY_FILE_PROFILES: tuple[tuple[str, str, str, str], ...] = (
    ("ACCOUNT_B", "Individual", "Fidelity Account B", OWNER_B),
)

# Fidelity cash sweep / money market — not tracked as equity lots
_SKIP_TICKERS = frozenset({"FDRXX", "SPAXX"})


def _is_hsa_export(path: Path) -> bool:
    return "HSA" in path.stem.upper()


def _is_ira_export(path: Path) -> bool:
    stem = path.stem.upper()
    return "IRA" in stem and "HSA" not in stem


def _is_llc_export(path: Path) -> bool:
    return "LLC" in path.stem.upper()


# Account numbers for the LLC export file names, from .env (comma-separated). Never commit real values.
_FIDELITY_LLC_ACCOUNT_IDS = frozenset(
    a.strip() for a in os.getenv("FIDELITY_LLC_ACCOUNT_IDS", "").split(",") if a.strip()
)


def _is_history_export(path: Path) -> bool:
    stem = path.stem.upper().replace(" ", "_")
    if "HISTORY_FOR_ACCOUNT" in stem:
        return True
    return any(aid in stem for aid in _FIDELITY_LLC_ACCOUNT_IDS)


def _is_append_only_export(path: Path) -> bool:
    """Recent activity exports should append, not wipe the Fidelity ledger."""
    return _is_history_export(path) or "ACCOUNTS_HISTORY" in path.stem.upper()


def _single_account_fund_for_export(path: Path) -> str | None:
    if _is_hsa_export(path):
        return _FIDELITY_HSA_FUND
    if _is_ira_export(path):
        return _FIDELITY_SG_IRA_FUND
    if _is_llc_export(path) or _is_history_export(path):
        return "Fidelity"
    return None


def _iter_fidelity_csv_rows(path: Path):
    """Yield dict rows; skip blank lines and find the Run Date header."""
    path = path.expanduser()
    with path.open(newline="", encoding="utf-8-sig") as f:
        lines = [ln for ln in f.readlines() if ln.strip()]
    if not lines:
        raise ValueError(f"{path.name}: empty file")
    header_idx = 0
    for i, ln in enumerate(lines):
        if "Run Date" in ln.split(",")[0] or ln.startswith("Run Date,"):
            header_idx = i
            break
    reader = csv.DictReader(lines[header_idx:])
    if not reader.fieldnames:
        raise ValueError(f"{path.name}: CSV has no header row")
    for row in reader:
        if not any(str(v or "").strip() for v in row.values()):
            continue
        if str(row.get("Run Date", "")).strip().startswith('"'):
            continue
        yield row


def resolve_fidelity_account_map(
    path: Path,
    overrides: dict[str, tuple[str, str]] | None = None,
) -> dict[str, tuple[str, str]]:
    base = _normalize_account_map(overrides)
    stem = path.stem.upper()
    single = _single_account_fund_for_export(path)
    if single == _FIDELITY_HSA_FUND:
        base[""] = (_FIDELITY_HSA_FUND, OWNER_A)
        base["Health Savings Account"] = (_FIDELITY_HSA_FUND, OWNER_A)
    elif single == _FIDELITY_SG_IRA_FUND:
        base[""] = (_FIDELITY_SG_IRA_FUND, OWNER_A)
        base["Traditional IRA"] = (_FIDELITY_SG_IRA_FUND, OWNER_A)
    elif single == "Fidelity":
        base[""] = ("Fidelity", OWNER_A)
        base["Limited Liability Company"] = ("Fidelity", OWNER_A)
    for token, account, fund_name, owner in _FIDELITY_FILE_PROFILES:
        if token in stem:
            base[account] = (fund_name, owner)
    return base


def _resolve_fund(
    account: str, account_map: dict[str, tuple[str, str]]
) -> tuple[str, str] | None:
    if account in account_map:
        return account_map[account]
    if not account and "" in account_map:
        return account_map[""]
    return None


def _row_get(row: dict[str, str], *keys: str) -> str:
    for k in keys:
        if k in row and row[k] is not None:
            return str(row[k])
    return ""


@dataclass(frozen=True)
class ParsedFidelityRow:
    trade_date: date
    fund_name: str
    owner: str
    ticker: str
    side: str
    shares: float
    price: float
    fees: float
    notes: str
    instrument_type: str = "stock"
    underlying: str | None = None
    expiry: date | None = None
    strike: float | None = None
    put_call: str | None = None
    contract_multiplier: float | None = None


def _parse_fidelity_date(raw: str) -> date:
    s = raw.strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not parse Fidelity date: {raw!r}")


def _float_cell(raw: str | None) -> float:
    if raw is None or str(raw).strip() == "":
        return 0.0
    v = parse_money_scalar(raw)
    import math

    return 0.0 if math.isnan(v) else float(v)


def _normalize_account_map(
    overrides: dict[str, tuple[str, str]] | None,
) -> dict[str, tuple[str, str]]:
    base = dict(DEFAULT_ACCOUNT_MAP)
    if overrides:
        base.update(overrides)
    return base


def _account_from_row(row: dict[str, str]) -> str:
    return _row_get(row, "Fund / Account", "Fund/Account", "Account").strip()


def _parse_side(action: str) -> str | None:
    if "YOU BOUGHT" in action:
        return "buy"
    if "REINVESTMENT" in action:
        return "buy"
    if "TRANSFERRED FROM" in action and "TRANSFERRED TO" not in action:
        return "buy"
    if "YOU SOLD" in action or "TRANSFERRED TO" in action:
        return "sell"
    if action.startswith("EXPIRED ") or " EXPIRED " in f" {action} ":
        return "sell"
    return None


def _resolve_option_fields(
    symbol: str,
    action: str,
    description: str,
) -> tuple[str, str, date | None, float | None, str | None, float | None] | None:
    """Return (occ, underlying, expiry, strike, put_call, multiplier) for option rows."""
    from ledger.option_symbols import parse_occ_symbol

    for candidate in (symbol, description, action):
        parsed = parse_occ_symbol(candidate)
        if parsed:
            return (
                parsed.occ_symbol,
                parsed.underlying,
                parsed.expiry,
                parsed.strike,
                parsed.put_call,
                parsed.multiplier,
            )
    return None


def _is_debit_card_purchase(row: dict[str, str]) -> bool:
    action = _row_get(row, "Action").upper()
    desc = _row_get(row, "Description").upper()
    return "DEBIT CARD PURCHASE" in action or "DEBIT CARD PURCHASE" in desc


def _parse_fidelity_row(
    row: dict[str, str],
    account_map: dict[str, tuple[str, str]],
    line_no: int,
) -> ParsedFidelityRow | None:
    if _is_debit_card_purchase(row):
        return None

    action_raw = _row_get(row, "Action").strip()
    action = action_raw.upper()
    account = _account_from_row(row)
    symbol = _row_get(row, "Symbol").strip().upper()
    description = _row_get(row, "Description").strip()

    fund_info = _resolve_fund(account, account_map)
    if fund_info is None:
        return None

    side = _parse_side(action)
    if side is None:
        return None

    option = _resolve_option_fields(symbol, action_raw, description)
    is_expired = action.startswith("EXPIRED ")

    if option is None:
        if not symbol or symbol in ("", " ") or symbol == "CASH" or re.fullmatch(r"\d+", symbol):
            return None
        if symbol in _SKIP_TICKERS:
            return None
        ticker = symbol
        instrument_type = "stock"
        underlying = expiry = strike = put_call = None
        multiplier = None
    else:
        ticker, underlying, expiry, strike, put_call, multiplier = option
        instrument_type = "option"

    fund_name, owner = fund_info
    trade_date = _parse_fidelity_date(_row_get(row, "Run Date"))

    qty = _float_cell(_row_get(row, "Quantity"))
    shares = abs(qty)
    if shares <= 0:
        return None

    price = _float_cell(_row_get(row, "Price ($)", "Price"))
    amount = _float_cell(_row_get(row, "Amount ($)", "Amount"))
    if price <= 0 and shares > 0 and amount != 0:
        # Option premiums are per share; amount includes ×100 and fees.
        if instrument_type == "option":
            mult = float(multiplier or 100.0)
            commission = _float_cell(_row_get(row, "Commission ($)", "Commission"))
            fees_col = _float_cell(_row_get(row, "Fees ($)", "Fees"))
            price = max(0.0, (abs(amount) - abs(commission) - abs(fees_col)) / (shares * mult))
        else:
            price = abs(amount) / shares

    if price < 0:
        return None
    if price == 0 and not is_expired and not (
        side == "buy" and ("RSU" in action or amount == 0)
    ):
        if instrument_type != "option":
            return None
        # Opening/closing options should have a premium; skip broken rows.
        if not is_expired and amount == 0:
            return None

    commission = _float_cell(_row_get(row, "Commission ($)", "Commission"))
    fees_col = _float_cell(_row_get(row, "Fees ($)", "Fees"))
    fees = commission + fees_col

    notes = action_raw
    if description:
        notes = f"{notes} — {description}"
    if account == "Individual - TOD":
        notes = f"[Individual-TOD] {notes}"

    return ParsedFidelityRow(
        trade_date=trade_date,
        fund_name=fund_name,
        owner=owner,
        ticker=ticker,
        side=side,
        shares=shares,
        price=price,
        fees=fees,
        notes=notes[:500],
        instrument_type=instrument_type,
        underlying=underlying,
        expiry=expiry,
        strike=strike,
        put_call=put_call,
        contract_multiplier=multiplier,
    )


def load_fidelity_rows(
    path: Path,
    account_map: dict[str, tuple[str, str]] | None = None,
) -> list[ParsedFidelityRow]:
    path = path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Fidelity CSV not found: {path.resolve()}")

    amap = (
        resolve_fidelity_account_map(path)
        if account_map is None
        else _normalize_account_map(account_map)
    )
    parsed: list[ParsedFidelityRow] = []
    skipped_accounts: set[str] = set()

    for i, row in enumerate(_iter_fidelity_csv_rows(path), start=2):
        account = _account_from_row(row)
        if account and account not in amap:
            skipped_accounts.add(account)
            continue
        raw_date = _row_get(row, "Run Date").strip()
        if not raw_date or not re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", raw_date):
            continue
        try:
            p = _parse_fidelity_row(row, amap, i)
        except ValueError as e:
            raise ValueError(f"{path.name} line {i}: {e}") from e
        if p is not None:
            parsed.append(p)

    if skipped_accounts:
        import warnings

        warnings.warn(
            f"Skipped unknown Fidelity account(s): {', '.join(sorted(skipped_accounts))}. "
            f"Known: {', '.join(sorted(amap.keys()))}"
        )

    # Oldest first so the trade ledger replays history correctly
    parsed.sort(key=lambda r: (r.trade_date, r.fund_name, r.ticker, r.side))
    return parsed


def _owner_for_fund(fund_name: str, account_map: dict[str, tuple[str, str]]) -> str:
    for name, owner in account_map.values():
        if name == fund_name:
            return owner
    return OWNER_A


def _existing_cash_event_keys(conn: sqlite3.Connection, fund_id: int) -> set[tuple[date, float, str]]:
    from ledger.trades_db import fetch_fund_cash_events

    return {
        (e.event_date, float(e.amount), (e.notes or "")[:200])
        for e in fetch_fund_cash_events(conn, fund_id)
    }


def _insert_cash_events_deduped(
    conn: sqlite3.Connection,
    fund_id: int,
    events: list[tuple[date, float, str]],
) -> int:
    existing = _existing_cash_event_keys(conn, fund_id)
    added = 0
    for evt_date, amount, note in events:
        key = (evt_date, amount, note[:200])
        if key in existing:
            continue
        insert_fund_cash_event(conn, fund_id, evt_date, amount, note)
        existing.add(key)
        added += 1
    return added


def load_fidelity_cash_contributions(
    path: Path,
    account_map: dict[str, tuple[str, str]] | None = None,
) -> list[tuple[str, date, float, str]]:
    """HSA/check transfers and similar cash-ins (not equity trades)."""
    path = path.expanduser()
    amap = (
        resolve_fidelity_account_map(path)
        if account_map is None
        else _normalize_account_map(account_map)
    )
    out: list[tuple[str, date, float, str]] = []
    for row in _iter_fidelity_csv_rows(path):
        account = _account_from_row(row)
        if account and account not in amap:
            continue
        resolved = _resolve_fund(account, amap)
        if resolved is None:
            continue
        fund_name, _owner = resolved
        action = _row_get(row, "Action").upper()
        is_transfer = "TRANSFER OF ASSETS CHECK RECEIVED" in action or "CHECK RECEIVED" in action
        is_contribution = "CASH CONTRIBUTION" in action
        if not is_transfer and not is_contribution:
            continue
        amt = _float_cell(_row_get(row, "Amount ($)", "Amount"))
        if amt <= 0:
            continue
        out.append(
            (
                fund_name,
                _parse_fidelity_date(_row_get(row, "Run Date")),
                amt,
                _row_get(row, "Action").strip()[:200],
            )
        )
    out.sort(key=lambda x: x[1])
    return out


def _is_equity_trade_row(row: dict[str, str]) -> bool:
    action = _row_get(row, "Action").upper()
    symbol = _row_get(row, "Symbol").strip().upper()
    if action.startswith("EXPIRED "):
        return True
    if not symbol or symbol in _SKIP_TICKERS:
        return False
    if "YOU BOUGHT" in action or "YOU SOLD" in action:
        return True
    if "REINVESTMENT" in action and symbol not in _SKIP_TICKERS:
        qty = _float_cell(_row_get(row, "Quantity"))
        return qty > 0
    return False


def load_fidelity_cash_events(
    path: Path,
    account_map: dict[str, tuple[str, str]] | None = None,
) -> list[tuple[str, date, float, str]]:
    """
    Non-trade cash flows: EFT deposits/withdrawals, debit card, cash dividends, etc.
    Excludes transfer-of-assets (opening cash) and equity trades.
    """
    path = path.expanduser()
    amap = (
        resolve_fidelity_account_map(path)
        if account_map is None
        else _normalize_account_map(account_map)
    )
    out: list[tuple[str, date, float, str]] = []
    for row in _iter_fidelity_csv_rows(path):
        account = _account_from_row(row)
        if account and account not in amap:
            continue
        resolved = _resolve_fund(account, amap)
        if resolved is None:
            continue
        fund_name, _owner = resolved
        if _is_equity_trade_row(row):
            continue
        action = _row_get(row, "Action").upper()
        if "ELECTRONIC FUNDS TRANSFER RECEIVED" in action or "ELECTRONIC FUNDS TRANSFER PAID" in action:
            amt = _float_cell(_row_get(row, "Amount ($)", "Amount"))
            if amt != 0:
                out.append(
                    (
                        fund_name,
                        _parse_fidelity_date(_row_get(row, "Run Date")),
                        amt,
                        _row_get(row, "Action").strip()[:200],
                    )
                )
            continue
        if (
            "TRANSFER OF ASSETS CHECK RECEIVED" in action
            or "CHECK RECEIVED" in action
            or "CASH CONTRIBUTION" in action
        ):
            continue
        symbol = _row_get(row, "Symbol").strip().upper()
        if symbol in _SKIP_TICKERS:
            continue
        amt = _float_cell(_row_get(row, "Amount ($)", "Amount"))
        if amt == 0:
            continue
        if "DEBIT CARD" in action or (
            "DIVIDEND RECEIVED" in action and _float_cell(_row_get(row, "Quantity")) == 0
        ) or "MARGIN INTEREST" in action:
            raw_date = _row_get(row, "Run Date").strip()
            if not raw_date or not re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", raw_date):
                continue
            out.append(
                (
                    fund_name,
                    _parse_fidelity_date(raw_date),
                    amt,
                    _row_get(row, "Action").strip()[:200],
                )
            )
    out.sort(key=lambda x: x[1])
    return out


def load_fidelity_reported_cash(
    path: Path,
) -> tuple[date, float] | None:
    """Latest Cash Balance from the export (first data row = most recent)."""
    path = path.expanduser()
    for row in _iter_fidelity_csv_rows(path):
        raw_date = _row_get(row, "Run Date").strip()
        if not raw_date or not re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", raw_date):
            continue
        raw = _row_get(row, "Cash Balance ($)", "Cash Balance", "Cash balance").strip()
        if not raw:
            continue
        bal = _float_cell(raw)
        return _parse_fidelity_date(raw_date), bal
    return None


def _fund_id_for_name(
    conn: sqlite3.Connection,
    fund_ids: dict[str, int],
    fund_name: str,
    owner: str,
) -> int:
    if fund_name not in fund_ids:
        fund = get_fund_by_name(conn, fund_name)
        if fund is None:
            fund = create_fund(conn, fund_name, owner, 0.0, "USD")
        fund_ids[fund_name] = fund.id
    return fund_ids[fund_name]


def _apply_fidelity_cash_flows(
    conn: sqlite3.Connection,
    path: Path,
    amap: dict[str, tuple[str, str]],
    *,
    fund_ids: dict[str, int] | None = None,
    replace_cash_events: bool = False,
) -> dict[str, int]:
    """Import EFT/deposit cash events (and opening contributions) per fund."""
    init_trade_schema(conn)
    fund_ids = {} if fund_ids is None else dict(fund_ids)
    cash_ins = load_fidelity_cash_contributions(path, amap)
    cash_events = load_fidelity_cash_events(path, amap)
    counts: dict[str, int] = {}

    touched: set[str] = {fund for fund, _, _, _ in cash_events}
    touched.update(fund for fund, _, _, _ in cash_ins)

    for fund_name in touched:
        _fund_id_for_name(
            conn,
            fund_ids,
            fund_name,
            _owner_for_fund(fund_name, amap),
        )

    if replace_cash_events:
        for fund_name in touched:
            delete_fund_cash_events(conn, fund_ids[fund_name])

    by_fund: dict[str, list[tuple[date, float, str]]] = {}
    for fund_name, evt_date, amount, note in cash_events:
        by_fund.setdefault(fund_name, []).append((evt_date, amount, note))

    for fund_name, events in by_fund.items():
        counts[fund_name] = _insert_cash_events_deduped(conn, fund_ids[fund_name], events)

    cont_by_fund: dict[str, list[tuple[date, float, str]]] = {}
    for fund_name, evt_date, amount, note in cash_ins:
        cont_by_fund.setdefault(fund_name, []).append((evt_date, amount, note))

    for fund_name, conts in cont_by_fund.items():
        total_in = sum(amt for _, amt, _ in conts)
        if total_in > 0:
            conn.execute(
                "UPDATE trade_funds SET opening_cash = ? WHERE id = ?",
                (total_in, fund_ids[fund_name]),
            )
            conn.commit()

    return counts


def import_fidelity_deposits(
    conn: sqlite3.Connection,
    path: Path,
    *,
    account_map: dict[str, tuple[str, str]] | None = None,
) -> dict[str, int]:
    """Import bank deposits / EFT cash flows without touching trades."""
    amap = (
        resolve_fidelity_account_map(path)
        if account_map is None
        else _normalize_account_map(account_map)
    )
    return _apply_fidelity_cash_flows(conn, path, amap)


def import_fidelity_csv(
    conn: sqlite3.Connection,
    path: Path,
    *,
    account_map: dict[str, tuple[str, str]] | None = None,
    replace_existing: bool = False,
) -> dict[str, int]:
    """
    Import Fidelity history CSV into trades table.
    Returns counts per fund name.
    """
    init_trade_schema(conn)
    amap = (
        resolve_fidelity_account_map(path)
        if account_map is None
        else _normalize_account_map(account_map)
    )
    single_fund = _single_account_fund_for_export(path)
    append_only = _is_append_only_export(path) and not replace_existing

    if single_fund and not append_only:
        fund = get_fund_by_name(conn, single_fund)
        if fund is not None:
            conn.execute("DELETE FROM trades WHERE fund_id = ?", (fund.id,))
            delete_fund_cash_events(conn, fund.id)
            conn.commit()

    rows = load_fidelity_rows(path, amap)
    reported_cash = load_fidelity_reported_cash(path) if single_fund else None
    counts: dict[str, int] = {}

    fund_ids: dict[str, int] = {}
    for r in rows:
        if r.fund_name not in fund_ids:
            fund = get_fund_by_name(conn, r.fund_name)
            if fund is None:
                fund = create_fund(conn, r.fund_name, r.owner, 0.0, "USD")
            fund_ids[r.fund_name] = fund.id

    if replace_existing and not (single_fund and not append_only):
        for fid in fund_ids.values():
            conn.execute("DELETE FROM trades WHERE fund_id = ?", (fid,))
        conn.commit()

    existing_keys: dict[int, set[tuple]] = {}
    if append_only:
        from ledger.trades_db import fetch_trades_for_fund

        for fund_name, fid in fund_ids.items():
            existing_keys[fid] = {
                (
                    t.trade_date,
                    t.side,
                    round(float(t.shares), 6),
                    t.ticker.upper(),
                    round(float(t.price), 6),
                )
                for t in fetch_trades_for_fund(conn, fid)
            }

    for r in rows:
        fid = fund_ids[r.fund_name]
        key = (
            r.trade_date,
            r.side,
            round(float(r.shares), 6),
            r.ticker.upper(),
            round(float(r.price), 6),
        )
        if append_only and key in existing_keys.get(fid, set()):
            continue
        insert_trade(
            conn,
            fid,
            r.trade_date,
            r.ticker,
            r.side,
            r.shares,
            r.price,
            r.fees,
            r.notes,
            instrument_type=r.instrument_type,
            underlying=r.underlying,
            expiry=r.expiry,
            strike=r.strike,
            put_call=r.put_call,
            contract_multiplier=r.contract_multiplier,
        )
        if append_only:
            existing_keys.setdefault(fid, set()).add(key)
        counts[r.fund_name] = counts.get(r.fund_name, 0) + 1

    cash_counts = _apply_fidelity_cash_flows(
        conn,
        path,
        amap,
        fund_ids=fund_ids,
        replace_cash_events=bool(single_fund) and not append_only,
    )
    counts.update({f"{k} cash": v for k, v in cash_counts.items()})

    if single_fund and single_fund in fund_ids and reported_cash is not None:
        rc_date, rc_bal = reported_cash
        upsert_fund_reported_cash(
            conn,
            fund_ids[single_fund],
            rc_date,
            rc_bal,
            source=path.name,
        )

    if _FIDELITY_HSA_FUND in fund_ids:
        delete_trades_for_fund_notes_like(conn, _FIDELITY_HSA_FUND, "DEBIT CARD PURCHASE")

    return counts
