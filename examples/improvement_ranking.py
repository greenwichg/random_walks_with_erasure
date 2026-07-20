"""Feedback-aware ranking & filtering for the Health Report's improvement recommendations (RC2.4).

A pure, deterministic leaf. It **does not generate** recommendations — the engine still produces the
same weakest-metric set with the same evidence and impact — it only **re-orders and filters** that set
using signals that already exist:

  * **lifecycle** (RC2.3, per recommendation): ``accepted`` / ``in_progress`` promote; ``completed`` and
    ``dismissed`` suppress (with deterministic reappearance);
  * **article feedback** (``rec_feedback``: like / dislike / ignore / read_later) as a **global
    receptivity prior** — article-level feedback carries no per-metric target, so it never re-ranks
    individual recommendations (that per-metric attribution is the deferred evaluation phase); instead a
    net-negative reader (more dislikes/ignores than likes/read-laters) gets **stickier** suppression of
    already-dismissed recommendations, honouring their evident "stop pushing recs at me".

Every decision is recorded on each recommendation's ``ranking`` object (rank / visible / priority /
reason / signals) so nothing is hidden. Deterministic: same inputs → same order, no randomness, no clock.
"""
from __future__ import annotations

# Lifecycle priority boosts (added on top of the base "worst metric first" ordering).
IN_PROGRESS_BOOST = 3     # the reader is actively working on it → keep it prominent
ACCEPTED_BOOST = 2        # the reader committed to it → keep it prominent

# Dismissed recommendations reappear only when the metric has regressed at least this many points
# below where it stood when the recommendation was first generated ("it got materially worse").
BASE_REAPPEAR_DROP = 8
# A net-negative reader (rejects recs) makes that bar higher — their dismissals stick harder.
NEG_RECEPTIVITY_PENALTY = 4

# Recommendations whose suggested actions substantially overlap — at most one of a family is shown.
_ACTION_FAMILY = {
    "viewpointBalance": "cross_cutting",
    "echoChamber": "cross_cutting",
    "openMindedness": "cross_cutting",
    "sourceDiversity": "sources",
    "topicDiversity": "topics",
    "reportingRatio": "register",
    "emotionalBalance": "emotion",
}

# Deterministic tie-break order (the engine's canonical metric order).
_METRIC_ORDER = ("topicDiversity", "sourceDiversity", "reportingRatio", "emotionalBalance",
                 "echoChamber", "viewpointBalance", "openMindedness")
_ORDER_INDEX = {k: i for i, k in enumerate(_METRIC_ORDER)}


def net_receptivity(feedback_counts: dict) -> int:
    """A reader's net receptivity to recommendations: ``(like + read_later) − (dislike + ignore)``.
    Each article-feedback signal contributes exactly ±1; the sign is all the ranker uses."""
    c = feedback_counts or {}
    return (int(c.get("like", 0)) + int(c.get("read_later", 0))
            - int(c.get("dislike", 0)) - int(c.get("ignore", 0)))


def _lifecycle_suppression(state, first, cur, reappear_drop):
    """Return ``(suppressed_reason, regressed_flag)`` from the lifecycle state. ``None`` reason = show."""
    if state == "completed":
        # Auto-reappears: RC2.3's reconciler flips a completed rec back to 'shown' if its metric
        # regresses below the completion bar, so suppressing on state == completed is sufficient here.
        return "completed", False
    if state == "dismissed":
        if first is not None and cur is not None and cur <= first - reappear_drop:
            return None, True                        # dismissed, but it got materially worse → resurface
        return "dismissed", False
    return None, False


def _priority(state, score):
    """Base priority = worst metric first (``−score``), plus the lifecycle promotion for a rec the
    reader has committed to."""
    p = -float(score if score is not None else 50)
    if state == "in_progress":
        p += IN_PROGRESS_BOOST
    elif state == "accepted":
        p += ACCEPTED_BOOST
    return p


def rank(improvements: list, feedback_counts: dict, scores: dict) -> list:
    """Re-order and filter the generated improvements (each may already carry a ``lifecycle`` object).

    Returns the SAME recommendation dicts, reordered (visible first, by rank; suppressed after), each
    with an added ``ranking`` object. Generation is untouched — this only ranks/filters. Deterministic."""
    net = net_receptivity(feedback_counts)
    reappear_drop = BASE_REAPPEAR_DROP + (NEG_RECEPTIVITY_PENALTY if net < 0 else 0)

    rows = []
    for imp in improvements:
        metric = imp.get("metric")
        lc = imp.get("lifecycle") or {}
        state = lc.get("state")
        score = scores.get(metric, lc.get("currentScore"))
        first = lc.get("firstScore")
        cur = lc.get("currentScore", score)
        reason, regressed = _lifecycle_suppression(state, first, cur, reappear_drop)
        signals = [{"signal": "metricScore", "effect": f"base priority {-int(score) if score is not None else 'n/a'}"}]
        if state in ("accepted", "in_progress"):
            signals.append({"signal": f"lifecycle:{state}",
                            "effect": f"+{IN_PROGRESS_BOOST if state == 'in_progress' else ACCEPTED_BOOST} priority"})
        if regressed:
            signals.append({"signal": "regressed_after_dismiss",
                            "effect": f"resurfaced (score fell ≥{reappear_drop} below first)"})
        if reason == "dismissed" and net < 0:
            signals.append({"signal": "receptivity:negative",
                            "effect": f"suppression stickier (net feedback {net})"})
        rows.append({"imp": imp, "metric": metric, "family": _ACTION_FAMILY.get(metric, metric),
                     "priority": _priority(state, score), "suppressed": reason, "signals": signals})

    # Diversity: among the not-yet-suppressed recs, keep only the highest-priority of each action family.
    survivors = [r for r in rows if r["suppressed"] is None]
    best_of_family: dict = {}
    for r in sorted(survivors, key=lambda r: (-r["priority"], _ORDER_INDEX.get(r["metric"], 99))):
        fam = r["family"]
        if fam in best_of_family:
            kept = best_of_family[fam]
            r["suppressed"] = f"overlaps:{fam}"
            r["signals"].append({"signal": f"diversity:{fam}",
                                 "effect": f"suppressed — overlaps {kept['metric']}"})
        else:
            best_of_family[fam] = r

    def _sort_key(r):
        return (-r["priority"], _ORDER_INDEX.get(r["metric"], 99))

    visible = sorted([r for r in rows if r["suppressed"] is None], key=_sort_key)
    hidden = sorted([r for r in rows if r["suppressed"] is not None], key=_sort_key)

    out = []
    for rank_pos, r in enumerate(visible, start=1):
        r["imp"]["ranking"] = {"rank": rank_pos, "visible": True, "priority": round(r["priority"], 2),
                               "reason": None, "signals": r["signals"]}
        out.append(r["imp"])
    for r in hidden:
        r["imp"]["ranking"] = {"rank": None, "visible": False, "priority": round(r["priority"], 2),
                               "reason": r["suppressed"], "signals": r["signals"]}
        out.append(r["imp"])
    return out
