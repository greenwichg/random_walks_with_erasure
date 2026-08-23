"""Reader-state context for a recommendation request — Tier 1 of the X-audit roadmap
(docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md).

Loads the two per-reader signal families the store has been *recording* all along and hands them
to the engine as plain ``params`` keys, closing the loop that ``store.RecFeedback`` explicitly
left open ("Recorded only (B1)… kept for a future consumer to decide how (if at all) to weigh
it"). This module is that consumer's first half: **data, not policy**. It reads the store and
produces id lists; every weight, floor, and cap lives in ``api_server._preference_rerank`` beside
the interest/country anchors, where the explain observer shares it verbatim (the 21a parity rule
— one implementation, or drift).

Two families, two flags, both **default OFF** so production serves byte-identical feeds until an
operator opts in (the strangler discipline: land dark, enable per cohort):

``RWE_REC_FEEDBACK=1`` → ``params["feedback"] = {"dislike": [...], "like": [...], "ignore": [...],
    "another_viewpoint": [...], "already_know": [...], "too_repetitive": [...],
    "fewer_from_source": [...], "more_topic": [...]}``
    The reader's explicit card feedback (``rec_feedback``). ``read_later`` counts as ``like`` —
    both say "more of this was welcome". The last five buckets are the Tier-2 vocabulary,
    passed through as recorded (data, not policy — their weights live beside the Tier-1 anchors
    in ``api_server._reader_state_factors``). Ids are article ids exactly as served
    (``article.id`` = the corpus item id), so the engine resolves them against the corpus it is
    currently ranking; an id that has rotated out simply matches nothing.

``RWE_REC_REPETITION=1`` → ``params["repetition"] = {"unopened": [...], "opened": [...]}``
    What the serve path already recorded in ``rec_events``: articles surfaced to this reader
    within the window (``RWE_REC_REPEAT_WINDOW_DAYS``, default 14), split by whether they were
    ever opened. The engine turns these into *decay*, never exclusion — a card the reader has
    scrolled past five times stops occupying a slot, without any article becoming unreachable.

Failure posture: additive. Any store error here must degrade to the sliders-only feed, never to
an error — the caller wraps :func:`attach_reader_state` accordingly, and the function itself
never raises for missing/empty state.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

__all__ = ["feedback_enabled", "repetition_enabled", "repeat_window_days",
           "attach_reader_state"]


def feedback_enabled() -> bool:
    """Whether explicit card feedback (like / dislike / ignore / read_later) shapes the feed."""
    return os.environ.get("RWE_REC_FEEDBACK", "0").strip() == "1"


def repetition_enabled() -> bool:
    """Whether previously-surfaced cards decay instead of recurring indefinitely."""
    return os.environ.get("RWE_REC_REPETITION", "0").strip() == "1"


def repeat_window_days() -> float:
    """How far back a surfaced card still counts as "recently shown". Outside the window the
    record is ignored entirely — an article the reader saw a month ago is fair game again,
    which is what keeps decay a freshness mechanism rather than a permanent blacklist."""
    try:
        return max(0.0, float(os.environ.get("RWE_REC_REPEAT_WINDOW_DAYS", "14")))
    except ValueError:
        return 14.0


#: Tier-2 vocabulary types passed through under their own names (recorded type = bucket name).
_VOCABULARY = ("another_viewpoint", "already_know", "too_repetitive",
               "fewer_from_source", "more_topic")


def _feedback_state(store, uid: int) -> "dict | None":
    fb: dict = {"dislike": [], "like": [], "ignore": [], **{t: [] for t in _VOCABULARY}}
    for row in store.list_recommendation_feedback(uid):
        kind = row.get("feedback")
        aid = str(row.get("articleId") or "")
        if not aid:
            continue
        if kind == "dislike":
            fb["dislike"].append(aid)
        elif kind in ("like", "read_later"):
            fb["like"].append(aid)
        elif kind == "ignore":
            fb["ignore"].append(aid)
        elif kind in _VOCABULARY:
            fb[kind].append(aid)
    return fb if any(fb.values()) else None


def _repetition_state(store, uid: int) -> "dict | None":
    since = (datetime.now(timezone.utc) - timedelta(days=repeat_window_days())).isoformat()
    unopened, opened = [], []
    for row in store.rec_events_state(uid, since=since):
        aid = str(row.get("articleId") or "")
        if not aid:
            continue
        (opened if row.get("opened") else unopened).append(aid)
    return ({"unopened": unopened, "opened": opened}
            if (unopened or opened) else None)


def attach_reader_state(params: "dict | None", store, uid: int) -> "dict | None":
    """Return ``params`` extended with whatever reader state the flags admit.

    Contract, pinned by tests: with both flags off — or with no recorded state — the input is
    returned **unchanged** (the very same object, ``None`` stays ``None``), so the engine's
    "params without these keys is the historical feed" identity holds by construction. The input
    dict is never mutated; extension copies."""
    if store is None:
        return params
    add: dict = {}
    if feedback_enabled():
        fb = _feedback_state(store, uid)
        if fb:
            add["feedback"] = fb
    if repetition_enabled():
        rep = _repetition_state(store, uid)
        if rep:
            add["repetition"] = rep
    if not add:
        return params
    out = dict(params) if params else {}
    out.update(add)
    return out
