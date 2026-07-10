"""Recommendation Validation Pipeline — Phase 1 (Commit 21d).

WHAT THIS IS
============
An offline, golden-fixture, PASS/FAIL pipeline that answers the question a user actually asks —
**"why on earth did I get this recommendation?"** — with proof, not trust. It mirrors the Metric
Validation Pipeline's shape (a package + a launcher + golden fixtures + a report + isolated run
history) but validates *behaviour* rather than reimplementing linear algebra.

Phase 1 reuses the production graph **and** the production ranking, then proves, on top of them:

    Stage 1 · Extract              a scenario fixture → a REAL offline recommendation case
    Stage 2 · Evidence Validation  every explanation's evidence is a SUBSET of the reader context it
                                   was handed — the resolver never invents a fact ("evidence ⊆ context")
    Stage 3 · Explanation Valid.   every explanation validate()s clean, is exactly one sentence, and
                                   the scenario's target resolves to its expected type
    Stage 4 · Determinism          identical inputs → byte-identical recommendations + explanations
    Stage 5 · Ranking Validation   seen-exclusion holds, nothing is fabricated (every rec is a real
                                   catalog node), and the feed CHANGES when the history changes

Independent RWE-B/RWE-D score recomputation from the FeedbackGraph — the "prove the matrix
multiplication" engineering check — is deliberately the *last*, optional stage, deferred to Phase 2
(21d.2): it is a fine exercise but less immediately valuable than validating the behaviour users
experience.

Nothing here modifies the recommender, the graph, the ranking, the Evidence Resolver, or the
explain endpoint — it is a read-only observer, exactly like the Metric Validation Pipeline.
"""
from dataclasses import dataclass


@dataclass
class Check:
    """One assertion in a stage: a stable name, PASS/FAIL, and a human-readable detail."""
    stage: str
    check: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"stage": self.stage, "check": self.check,
                "passed": self.passed, "detail": self.detail}


#: The one sentence-signature per explanation type — used to prove explanations are NEVER combined
#: (exactly one signature per message). Kept here (not in the resolver) so the resolver stays
#: untouched; a drift between this list and the resolver's wording is caught by the pipeline tests.
SENTENCE_SIGNATURES = {
    "story_match": ("covered the same story", "latest update", "following this story"),
    "topic_continuity": ("You've been reading about",),
    "new_publisher": ("broadens your source diversity",),
    "bridge": ("another political perspective",),
    "long_tail": ("less frequently recommended",),
    "coverage_breadth": ("Broadens your",),
}
