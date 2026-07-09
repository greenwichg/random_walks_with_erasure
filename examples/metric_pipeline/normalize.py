"""Stage 2 · Normalize — coerce heterogeneous read rows to the one canonical scored contract.

A read may arrive as a ``store.list_reads`` row (``{"scored": {...}, ...}``), a bare ``scored`` dict,
or a golden fixture row. Every downstream stage sees only a list of ``{"scored": {...}}`` dicts with
the fields the metrics use, so this is the single place shape differences are reconciled.

No value is invented: missing fields stay missing (the metrics already degrade to ``n/a``); we only
unwrap the envelope and coerce types (``lean`` → float-or-None, ``political`` → bool, ``emotion`` →
a full 5-bucket share dict or None). Keeping this the *only* interpretation point is what lets the
independent engine and the production engine be fed provably identical inputs in Stage 6/7.
"""
from __future__ import annotations

import math
from typing import List, Optional

from study_metrics import EMOTION_BUCKETS

_STR_FIELDS = ("category", "outlet", "register", "title", "subcategory")


def _num(x) -> Optional[float]:
    """A finite float, or ``None`` (so a blank / unparsable numeric degrades to n/a, not 0.0)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def normalize_read(row: dict) -> dict:
    """One read row → a clean ``{"scored": {...}}`` dict (unwrap, coerce lean/political/emotion)."""
    s = dict(row.get("scored", row) or {})
    out: dict = {}
    for k in _STR_FIELDS:
        v = s.get(k)
        out[k] = v.strip() if isinstance(v, str) else v
    out["lean"] = _num(s.get("lean"))
    out["political"] = bool(s.get("political"))
    out["confidence"] = _num(s.get("confidence"))
    emo = s.get("emotion")
    if isinstance(emo, dict) and emo:
        out["emotion"] = {b: (_num(emo.get(b)) or 0.0) for b in EMOTION_BUCKETS}
    else:
        out["emotion"] = None
    read = {"scored": out}
    # ``study_metrics.reading_time`` reads ``readingMinutes`` at the *row* top level (not in scored),
    # so carry it there untouched when a source provides it.
    if row.get("readingMinutes") is not None:
        read["readingMinutes"] = row["readingMinutes"]
    return read


def normalize(rows: List[dict]) -> List[dict]:
    """Normalize a list of read rows to canonical ``{"scored": {...}}`` dicts."""
    return [normalize_read(r) for r in rows]
