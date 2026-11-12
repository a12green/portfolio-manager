"""Regression guard: every value put into HTML by app.js must go through esc()."""

from __future__ import annotations

import re
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "app.js"

# Lines that build markup. Plain text assignments (textContent) are not checked.
_MARKUP = re.compile(r"<(td|tr|th|option|span|strong|br|button|div)\b|innerHTML|insertAdjacentHTML")
# Interpolations that are numbers or fixed strings, so escaping is not needed.
_SAFE_PREFIXES = ("esc(",)


def _interpolations(line: str):
    i = 0
    while True:
        j = line.find("${", i)
        if j < 0:
            return
        depth, k = 1, j + 2
        while k < len(line) and depth:
            depth += {"{": 1, "}": -1}.get(line[k], 0)
            k += 1
        yield line[j + 2 : k - 1].strip()
        i = k


def test_markup_interpolations_are_escaped():
    offenders = []
    for n, line in enumerate(APP_JS.read_text(encoding="utf-8").splitlines(), start=1):
        if "${" not in line or not _MARKUP.search(line):
            continue
        for expr in _interpolations(line):
            if not expr.startswith(_SAFE_PREFIXES):
                offenders.append(f"{APP_JS.name}:{n}: ${{{expr}}}")
    assert not offenders, "unescaped values in markup:\n" + "\n".join(offenders)


def test_escape_helper_is_defined():
    text = APP_JS.read_text(encoding="utf-8")
    assert "const esc = " in text
    assert "&lt;" in text and "&quot;" in text
