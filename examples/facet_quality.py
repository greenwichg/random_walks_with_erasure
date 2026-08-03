"""facet_quality.py — is a facet extraction stable enough to be counted?

Design: docs/COVERAGE_COMPARISON_REVISED_DESIGN.md §11.5. Phase 0b's central question, and the
one the design says can stop a tier:

    Counting unreliable labels produces a precise-looking number over noisy input.

"7 of 9 outlets frame this as economic consequences" is only a fact if a second extraction of the
same nine articles produces the same nine labels. This module measures that — nothing here calls a
model, hits a network or reads a store; it takes facet records that were already generated and
reports how much two extractions agree.

Two measures, because the fields are two shapes and one number would flatter the harder one:

* **Cohen's κ** for the single-valued enums (``format``, ``depth``, ``centeredVoice``). Raw
  agreement alone is misleading when a category dominates — a model that answers "news_report"
  every time scores 95% agreement and has told you nothing. κ discounts the agreement expected by
  chance, which is exactly the flattery to remove. ``None`` is a real category here ("the model
  declined"), not missing data: stability of the refusal matters as much as stability of a choice.
* **Jaccard** for the set-valued fields (``frames``, ``voices`` roles, ``quantities``), where κ
  does not apply because a rater assigns several labels at once.

Interpretation follows the usual Landis–Koch bands, and the design's ship bar is κ ≥ 0.6
("substantial") for the fields a tier depends on.
"""

from __future__ import annotations

import math
from typing import Optional

#: Design §13: a tier ships only if the fields it uses clear this. Chosen before the data, and
#: named here so the bar cannot quietly move once a number comes back.
KAPPA_SHIP_BAR = 0.6

#: Landis & Koch (1977), the conventional reading of κ.
_BANDS = ((0.81, "almost perfect"), (0.61, "substantial"), (0.41, "moderate"),
          (0.21, "fair"), (0.0, "slight"), (-1.0, "none/worse than chance"))

#: The single-valued enums, and the set-valued ones. Kept here so the report, the tests and any
#: future tier all read one list.
SINGLE_FIELDS = ("format", "depth", "centeredVoice")
SET_FIELDS = ("frames", "voices", "quantities")


def band(k: Optional[float]) -> str:
    if k is None:
        return "n/a"
    for floor, label in _BANDS:
        if k >= floor:
            return label
    return "none"


def cohens_kappa(a: list, b: list) -> Optional[float]:
    """Agreement between two raters on the same items, chance-corrected.

    ``a[i]`` and ``b[i]`` are the two extractions of item ``i``; values may be ``None``, which is
    treated as its own category. Returns ``None`` when there is nothing to measure, and **1.0 when
    both raters used a single identical category throughout** — the degenerate case where the
    chance-agreement term is 1 and the usual formula divides by zero. That case is perfect
    agreement on a constant, which is honest to report as such; the accompanying category counts
    are what tell a reader the constant was uninformative."""
    pairs = [(x, y) for x, y in zip(a, b)]
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for x, y in pairs if x == y) / n
    cats = {x for x, _ in pairs} | {y for _, y in pairs}
    expected = sum((sum(1 for x, _ in pairs if x == c) / n)
                   * (sum(1 for _, y in pairs if y == c) / n) for c in cats)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def _single(facets: dict, field: str):
    return (facets or {}).get(field)


def _set(facets: dict, field: str) -> frozenset:
    """The comparable shape of a set-valued facet.

    Only the parts a comparison would actually count: a frame's key, a voice's ROLE (not the name,
    which is free text and would make every extraction disagree), and a quantity's kind+value
    (not its subject, for the same reason). Measuring stability of the free text would answer a
    question no tier asks."""
    items = (facets or {}).get(field) or []
    if field == "frames":
        return frozenset(str(i.get("key")) for i in items if isinstance(i, dict))
    if field == "voices":
        return frozenset(str(i.get("role")) for i in items if isinstance(i, dict))
    if field == "quantities":
        return frozenset((str(i.get("kind")), float(i.get("value")))
                         for i in items if isinstance(i, dict) and i.get("value") is not None)
    return frozenset()


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0                      # both said "nothing here" — agreement, not absence of it
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def agreement(run_a: dict, run_b: dict) -> dict:
    """Compare two extractions of the same articles.

    ``run_a`` / ``run_b`` map ``article_id -> facets``. Only ids present in both are compared, and
    the count is reported so a thin overlap cannot masquerade as a strong result."""
    ids = sorted(set(run_a) & set(run_b))
    out: dict = {"n": len(ids), "fields": {}}
    if not ids:
        return out
    for f in SINGLE_FIELDS:
        a = [_single(run_a[i], f) for i in ids]
        b = [_single(run_b[i], f) for i in ids]
        k = cohens_kappa(a, b)
        out["fields"][f] = {
            "kind": "kappa", "value": k, "band": band(k),
            "raw_agreement": sum(1 for x, y in zip(a, b) if x == y) / len(ids),
            "categories": len({x for x in a + b}),
            "ships": (k is not None and k >= KAPPA_SHIP_BAR),
        }
    for f in SET_FIELDS:
        scores = [_jaccard(_set(run_a[i], f), _set(run_b[i], f)) for i in ids]
        mean = sum(scores) / len(scores)
        out["fields"][f] = {
            "kind": "jaccard", "value": mean, "band": band(mean),
            "exact": sum(1 for s in scores if s == 1.0) / len(scores),
            "ships": mean >= KAPPA_SHIP_BAR,
        }
    return out


def throughput(p50_ms: float, *, interval_s: float = 600.0, concurrency: int = 1,
               headroom: float = 1.5) -> dict:
    """Turn a measured latency into the two operational numbers (design §9.3).

    ``per_cycle`` is what one cycle can actually finish at this latency — the ceiling the batch cap
    must not be set above, because generation inside a cycle is bounded by the poll interval and an
    overrunning cycle has its successor dropped by the single-flight lock. ``per_day`` is that
    against the ingest rate."""
    per_call_s = max(0.001, p50_ms / 1000.0)
    per_cycle = int((interval_s * max(1, concurrency)) / per_call_s)
    cycles_per_day = 86400.0 / interval_s
    return {"per_call_s": per_call_s, "per_cycle_capacity": per_cycle,
            "per_day_capacity": int(per_cycle * cycles_per_day),
            "headroom": headroom}


def batch_for(arrivals_per_day: float, *, interval_s: float = 600.0,
              headroom: float = 1.5) -> int:
    """The batch size that keeps up with ``arrivals_per_day`` (design §9.3's formula)."""
    cycles_per_day = 86400.0 / interval_s
    return max(1, math.ceil((max(0.0, arrivals_per_day) / cycles_per_day) * headroom))
