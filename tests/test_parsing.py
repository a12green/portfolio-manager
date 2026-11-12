"""Money, percentage, option-symbol and settings parsing."""

from __future__ import annotations

import json
import math

import pytest

import config
from ledger.money import parse_money_scalar, parse_pct_scalar
from ledger.option_symbols import is_occ_symbol, parse_occ_symbol


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$1,234.56", 1234.56),
        ("(100)", -100.0),
        ("-42.5", -42.5),
        (7, 7.0),
        ("12", 12.0),
    ],
)
def test_parse_money_accepts_common_formats(raw, expected):
    assert parse_money_scalar(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "abc"])
def test_parse_money_returns_nan_for_garbage(raw):
    assert math.isnan(parse_money_scalar(raw))


def test_parse_pct_accepts_percent_sign():
    assert parse_pct_scalar("26.03%") == pytest.approx(26.03)
    assert parse_pct_scalar("8.5") == pytest.approx(8.5)


def test_parse_pct_returns_nan_for_none():
    assert math.isnan(parse_pct_scalar(None))


def test_occ_symbol_parses_call_contract():
    contract = parse_occ_symbol("AAPL260618C00150000")
    assert contract is not None
    assert contract.underlying == "AAPL"
    assert contract.put_call == "call"
    assert contract.strike == pytest.approx(150.0)


def test_occ_symbol_rejects_plain_ticker():
    assert parse_occ_symbol("NVDA") is None
    assert not is_occ_symbol("NVDA")


def test_settings_example_loads_three_owners():
    assert config.OWNERS == ("Household A", "Household B", "Household C")
    assert (config.OWNER_A, config.OWNER_B, config.OWNER_C) == config.OWNERS


def test_settings_loader_rejects_wrong_owner_count(tmp_path, monkeypatch):
    import importlib

    bad = tmp_path / "settings.json"
    bad.write_text(json.dumps({"owners": ["Only One"]}))
    monkeypatch.setenv("SETTINGS_PATH", str(bad))
    with pytest.raises(ValueError, match="exactly three"):
        importlib.reload(config)
    monkeypatch.delenv("SETTINGS_PATH")
    importlib.reload(config)


def test_settings_loader_rejects_duplicate_owners(tmp_path, monkeypatch):
    import importlib

    bad = tmp_path / "settings.json"
    bad.write_text(json.dumps({"owners": ["Same", "Same", "Other"]}))
    monkeypatch.setenv("SETTINGS_PATH", str(bad))
    with pytest.raises(ValueError, match="exactly three"):
        importlib.reload(config)
    monkeypatch.delenv("SETTINGS_PATH")
    importlib.reload(config)
