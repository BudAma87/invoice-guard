"""String helpers for identity and matching rules."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")
_WS = re.compile(r"\s+")


def normalise_invoice_no(raw: str | None) -> str | None:
    """'inv- 1023/a' -> 'INV1023A'. Whitespace and punctuation removed, upper-cased."""
    if raw is None:
        return None
    out = _NON_ALNUM.sub("", raw).upper()
    return out or None


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    """Description similarity in [0, 1], case- and whitespace-insensitive."""
    na = _WS.sub(" ", a.lower()).strip()
    nb = _WS.sub(" ", b.lower()).strip()
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()
