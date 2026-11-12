from __future__ import annotations

import math
from typing import Any


def parse_money_scalar(val: Any) -> float:
    """Parse currency strings (e.g. '$1,234.56', '(100)') into float; NaN if unparseable."""
    if val is None:
        return float("nan")
    if isinstance(val, (int, float)):
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return float("nan")
        return float(val)
    s = str(val).strip().strip('"').strip("'")
    if s in ("", "#N/A", "N/A", "-", "--"):
        return float("nan")
    s = s.replace("$", "").replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return float("nan")


def parse_pct_scalar(val: Any) -> float:
    """Parse percent strings (e.g. '26.03%', '8.5') into float; NaN if unparseable."""
    if val is None:
        return float("nan")
    if isinstance(val, (int, float)):
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return float("nan")
        return float(val)
    s = str(val).strip().strip('"').strip("'")
    if s in ("", "#N/A", "N/A", "-", "--"):
        return float("nan")
    s = s.replace("%", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return float("nan")
