"""Owner names come from settings.json (copy settings.example.json and edit).

Everything else in the app refers to the three owners as OWNER_A, OWNER_B and
OWNER_C, so no real names live in the code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SETTINGS_PATH = Path(os.getenv("SETTINGS_PATH") or _HERE / "settings.json")
if not _SETTINGS_PATH.exists():
    _SETTINGS_PATH = _HERE / "settings.example.json"

OWNERS: tuple[str, ...] = tuple(
    str(name).strip() for name in json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))["owners"]
)
if len(OWNERS) != 3 or len(set(OWNERS)) != 3 or not all(OWNERS):
    raise ValueError(f"{_SETTINGS_PATH.name}: 'owners' must list exactly three different names")

OWNER_A, OWNER_B, OWNER_C = OWNERS
