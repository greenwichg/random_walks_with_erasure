"""Cohort + shadow harness for recommendation features — Tier 2 of the X-audit roadmap
(docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md, Phase 13.9: "Shadow first, cohort second, always
against the current blend as control").

X ships experiments as params + holdouts resolved per request; the shape adopted here is the
smallest honest version of that: **deterministic hash cohorts** (no coin flips — the same reader
is always in the same arm, across restarts and replicas, with no coordination) and **recorded
assignments** (the ``experiment_assignments`` table exists so an analysis can audit exactly who
was in what, rather than re-deriving membership from a hash it hopes matches). The harness
carries no policy: which features consult it, and what treatment means, live at the feature's
own gate.

Two env surfaces, both default-empty so production behaviour is byte-identical until an operator
opts in:

``RWE_REC_EXPERIMENT`` — ``"feature:pct[,feature:pct…]"`` (e.g. ``story_source:50``)
    Restricts an ENABLED feature (its own flag still decides existence and size) to the
    treatment cohort: readers hashing into ``pct`` get the feature, everyone else gets the
    control feed. A feature not named here is unrestricted — the flag alone governs — so the
    Tier-1 "flag on = on for everyone" semantics are unchanged unless an experiment is declared.

``RWE_REC_SHADOW`` — ``"feature[,feature…]"``
    Compute the would-be feed WITH the feature for readers who are not being served it, record
    its composition metrics under ``kind="shadow:<feature>"``, and serve the control feed
    untouched. The doc's shadow-log step: measure the change's effect on real requests before a
    single reader sees it. Costs one extra ranking pass per request while enabled — an operator
    choice, bounded and observable (the shadow kind's own ``feed_served_total``).

Failure posture: assignment RECORDING is best-effort (a store hiccup must never decide an arm or
break a feed); the ARM ITSELF is pure arithmetic and cannot fail.
"""

from __future__ import annotations

import hashlib
import os

__all__ = ["experiment_pct", "shadow_features", "cohort_of", "assign"]

#: Feature names the harness recognises today — the Tier-2 sources. Parsing is restricted to
#: known names so a typo in an env value surfaces as "experiment not applied" in verify-recs
#: (the declared spec vs the parsed one) rather than as a silently ignored gate.
KNOWN_FEATURES = ("story_source", "emerging", "blindspot_v2")


def _spec() -> dict:
    """``RWE_REC_EXPERIMENT`` parsed to ``{feature: pct}``. Junk entries are dropped, pct is
    clamped to [0, 100]. Empty/unset → {} (no experiments; flags govern alone)."""
    out: dict = {}
    raw = os.environ.get("RWE_REC_EXPERIMENT", "").strip()
    if not raw:
        return out
    for part in raw.split(","):
        name, _, pct = part.strip().partition(":")
        name = name.strip()
        if name not in KNOWN_FEATURES:
            continue
        try:
            out[name] = max(0, min(100, int(pct.strip())))
        except (TypeError, ValueError):
            continue
    return out


def experiment_pct(feature: str) -> "int | None":
    """The declared treatment percentage for ``feature``, or ``None`` when no experiment is
    declared for it (= the flag alone governs)."""
    return _spec().get(feature)


def shadow_features() -> tuple:
    """The features ``RWE_REC_SHADOW`` asks to shadow-log, restricted to known names."""
    raw = os.environ.get("RWE_REC_SHADOW", "").strip()
    if not raw:
        return ()
    return tuple(n for n in (p.strip() for p in raw.split(",")) if n in KNOWN_FEATURES)


def cohort_of(user_id: int, feature: str, pct: int) -> str:
    """``"treatment"`` or ``"control"`` — pure and deterministic.

    The bucket is the first 8 hex digits of ``sha256("<feature>:<user_id>")`` mod 100, so
    assignment is stable across processes, restarts, and replicas with no shared state, and
    INDEPENDENT across features (the feature name salts the hash — the same readers are not
    "always the guinea pigs" for every experiment). ``pct=100`` → everyone treated, ``0`` →
    no one; both ends behave exactly like the flag-only semantics they shade into."""
    bucket = int(hashlib.sha256(f"{feature}:{int(user_id)}".encode()).hexdigest()[:8], 16) % 100
    return "treatment" if bucket < int(pct) else "control"


def assign(store, user_id: int, feature: str) -> "str | None":
    """The reader's arm for ``feature`` under the CURRENT env spec, with the assignment recorded
    for audit — or ``None`` when no experiment is declared (caller: the flag alone governs).

    Recording is write-once per (user, experiment) and best-effort: the arm is computed from the
    hash regardless, so a store failure degrades to an unrecorded-but-correct assignment, never
    to a different feed."""
    pct = experiment_pct(feature)
    if pct is None:
        return None
    arm = cohort_of(user_id, feature, pct)
    try:
        if store is not None:
            store.record_experiment_assignment(int(user_id), feature, arm)
    except Exception:
        pass
    return arm
