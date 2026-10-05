"""Shared validation for LLM roles that write free-text argued from a
snapshot (spec §5.7.3: no digits outside evidence_ref placeholders, every
evidence_ref must resolve to a real snapshot field). Used by synthesis.py
and bull_bear.py so both apply the identical rule instead of two
slightly-different regexes drifting apart.
"""
from __future__ import annotations

import re

# Allow years (4 digits) — anything else is a number the model wrote into
# free text instead of using evidence_ref.
_ALLOWED_DIGIT_PATTERN = re.compile(r"\b(19|20)\d{2}\b")

# Indicator *names* the model may reasonably write in prose (spec §5.7.3's
# ban targets fabricated quantities, not a technical term that happens to
# contain a digit) — strictly the field names pipeline/indicators.py
# produces (ma20/ma50/ma200/rsi14/atr14/volume_avg20), case-insensitive,
# optionally with a space before the number ("RSI 14"). Not a general
# digit-allowance: a value like "RSI14 is 62" still fails on the literal 62.
_ALLOWED_INDICATOR_NAME_PATTERN = re.compile(
    r"\b(MA|RSI|ATR)\s?(20|50|200|14)\b|\bvolume[\s_]?avg\s?20\b", re.IGNORECASE
)

# spec §5.7.3: the model may reference a snapshot field via a {{dotted.path}}
# placeholder that code fills in later — field names legitimately contain
# digits (rsi14, ma50, ma200, macd...), so strip whole placeholders before
# checking for stray digits, not just the 4-digit-year allowance.
_PLACEHOLDER_PATTERN = re.compile(r"\{\{[^}]*\}\}")


def has_disallowed_digits(text: str) -> bool:
    stripped = _PLACEHOLDER_PATTERN.sub("", text)
    stripped = _ALLOWED_INDICATOR_NAME_PATTERN.sub("", stripped)
    stripped = _ALLOWED_DIGIT_PATTERN.sub("", stripped)
    return any(ch.isdigit() for ch in stripped)


def lookup_evidence_ref(snapshot: dict, ref: str) -> bool:
    node = snapshot
    for part in ref.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True
