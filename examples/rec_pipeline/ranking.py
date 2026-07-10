"""Stage 5 · Ranking Validation (production, behavioural).

Phase 1 reuses the production ranking, so this stage validates *properties* of it rather than
recomputing it (that is Phase 2 / 21d.2). Three properties, each a real product-risk guard:

* **seen-exclusion** — a reader is never recommended an article they already read;
* **no fabrication** — every recommended article is a real catalog node, not an invented row;
* **history-sensitivity** — the feed CHANGES when the history changes. Together with Stage 4
  (determinism), this brackets the recommender: same history → same feed (never random), different
  history → different feed (genuinely personalized). A recommender that returned the same feed for
  every reader would pass determinism and fail here.
"""
from __future__ import annotations

import evidence_resolver as er

from . import Check


def run(case, *, rebuild_perturbed=None) -> list:
    checks: list = []
    read_urls = set(case.reads)
    served = [er._canon(str((r.get("article") or {}).get("url")
                            or (r.get("article") or {}).get("id") or "")) for r in case.recs]

    # seen-exclusion
    leaked = [u for u in served if u in read_urls]
    checks.append(Check("ranking", "no recommendation is an article the reader already read",
                        not leaked, f"{len(leaked)} seen article(s) leaked into the feed"))

    # no fabrication — every served article exists in the catalog
    fabricated = [u for u in served if u and u not in case.catalog_by_url]
    checks.append(Check("ranking", "every recommendation is a real catalog article",
                        not fabricated, f"{len(fabricated)} article(s) not in the catalog"))

    checks.append(Check("ranking", "the feed is non-empty", len(case.recs) > 0,
                        f"{len(case.recs)} recommendations"))

    # history-sensitivity — a different history yields a different feed. Compare the ORDERED feed
    # (not just the set): appending reads re-weights the walk, so the ranking must move even when
    # the same articles remain eligible. A feed that is byte-identical after distinctive new reads
    # would mean the recommender ignores that history — the real risk this guards against.
    if rebuild_perturbed is not None and case.measured:
        perturbed = rebuild_perturbed(case.name)
        if perturbed is not None:
            after = [er._canon(str((r.get("article") or {}).get("url")
                                   or (r.get("article") or {}).get("id") or "")) for r in perturbed.recs]
            set_changed = set(served) != set(after)
            order_changed = served != after
            detail = ("feed byte-identical after appending distinctive reads" if not order_changed
                      else (f"{len(set(served) ^ set(after))} article(s) differ" if set_changed
                            else "same articles, re-ranked order"))
            checks.append(Check("ranking", "the feed changes when the reading history changes",
                                order_changed, detail))
    return checks
