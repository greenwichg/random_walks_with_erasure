"""Stage 4 · Determinism.

Recommendations must be a deterministic consequence of the reading history — identical inputs
produce byte-identical feeds and explanations. If a feed reshuffled on every refresh with no state
change, "why did I get this?" would have no stable answer. Two levels:

* **resolver determinism** — re-resolving the same feed K times is identical (cheap);
* **pipeline determinism** — rebuilding the whole case from the fixture a second time yields the
  same served order and the same explanations (the strong, end-to-end check — proves the corpus
  build, the walk, ranking, blend, and dedup carry no hidden randomness).
"""
from __future__ import annotations

import evidence_resolver as er

from . import Check


def _feed_signature(recs: list) -> list:
    return [str((r.get("article") or {}).get("id") or "") for r in recs]


def _explanations(recs: list, ctx: dict, index: dict) -> list:
    return [er.resolve(r, ctx, index).get("message") for r in recs]


def run(case, *, repeats: int = 3, rebuild=None) -> list:
    checks: list = []

    base_feed = _feed_signature(case.recs)
    base_exp = _explanations(case.recs, case.context, case.index)

    # resolver determinism — the explanation of a fixed feed never varies
    stable = all(_explanations(case.recs, case.context, case.index) == base_exp
                 for _ in range(repeats))
    checks.append(Check("determinism", f"explanations identical across {repeats} re-resolves",
                        stable, "" if stable else "resolver output varied on identical input"))

    # pipeline determinism — a full rebuild reproduces the same feed + explanations
    if rebuild is not None:
        case2 = rebuild(case.name)
        feed_same = _feed_signature(case2.recs) == base_feed
        exp_same = _explanations(case2.recs, case2.context, case2.index) == base_exp
        checks.append(Check("determinism", "a full rebuild reproduces the same feed order",
                            feed_same, "" if feed_same else "served order changed on rebuild"))
        checks.append(Check("determinism", "a full rebuild reproduces the same explanations",
                            exp_same, "" if exp_same else "explanations changed on rebuild"))
    return checks
