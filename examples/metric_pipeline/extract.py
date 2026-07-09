"""Stage 1 · Extract — pull a reader's reads from a source into a raw list of read rows.

Sources (all strictly read-only):
  * a **golden** persona by name — ``examples/metric_pipeline/golden/<name>.json``;
  * an arbitrary **JSON file** of reads (a ``{"reads": [...]}`` envelope or a bare list of read rows,
    in the ``store.list_reads`` shape or bare ``scored`` dicts);
  * a **live user** id via a ``Store`` instance (``store.list_reads(uid)``).

Extract interprets nothing — it only returns the rows exactly as the source holds them. Stage 2
(Normalize) reconciles their shape; every later stage sees the normalized form only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# The six pinned personas (the reference population for the percentile layer). Ordered so
# ``golden("all")`` builds a deterministic, reproducible population.
GOLDEN_NAMES = ("balanced", "echo_chamber", "opinion_heavy", "technology",
                "single_publisher", "global_reader")


def _rows_from_payload(payload) -> List[dict]:
    """A golden/history JSON is either a ``{"reads": [...]}`` envelope or a bare list of read rows."""
    if isinstance(payload, dict):
        return list(payload.get("reads") or [])
    return list(payload or [])


def extract_file(path: str) -> List[dict]:
    """Read rows from a JSON file (a ``{"reads": [...]}`` envelope or a bare list)."""
    with open(path, encoding="utf-8") as f:
        return _rows_from_payload(json.load(f))


def golden_meta(name: str) -> dict:
    """The persona's metadata (``name``/``description``/``expected`` notes), sans reads."""
    with open(GOLDEN_DIR / f"{name}.json", encoding="utf-8") as f:
        payload = json.load(f)
    return {k: v for k, v in payload.items() if k != "reads"} if isinstance(payload, dict) else {}


def extract_golden(name: str) -> List[dict]:
    """One golden persona's read rows."""
    path = GOLDEN_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"unknown golden dataset '{name}' (have: {', '.join(GOLDEN_NAMES)})")
    return extract_file(str(path))


def extract_golden_population() -> Dict[str, List[dict]]:
    """Every persona keyed by name — the pinned reference *population* for the percentile layer."""
    return {name: extract_golden(name) for name in GOLDEN_NAMES}


def extract_user(store, user_id: int) -> List[dict]:
    """A live user's reads via a ``Store`` instance (``store.list_reads``), newest-first — read-only."""
    return list(store.list_reads(int(user_id)))
