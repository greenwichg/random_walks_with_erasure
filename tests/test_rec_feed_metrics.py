"""Feed-quality counters — the Tier-1 evaluation framework, recorded where the feed is
assembled (docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md: "one code path feeds eval and dashboards").

These extend ``record_feed_composition``'s existing counters; the originals are pinned in
``test_rec_pipeline.py`` and must be untouched by the extension (its tests keep passing with no
edits — the back-compat proof). Here: HHI in basis points, distinct topics, story duplication
through an article-id resolver, repetition against the ids the request ranked with, blind-spot
coverage against the report's topics — and the no-kwargs call stays valid, because older callers
(and the explain observer's absence of metrics) must not need editing to survive this change.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import api_server as _engine  # noqa: E402
import obs_metrics  # noqa: E402


def _counters():
    snap = obs_metrics.snapshot()
    return dict(snap.get("counters") or {})


def _feed(rows):
    """rows: (publisher, topic, article_id, cross) tuples → serialized-rec shapes."""
    return [{"article": {"publisher": p, "topic": t, "id": a}, "crossCutting": c}
            for p, t, a, c in rows]


def test_quality_counters_measure_one_feed():
    before = _counters()
    feed = _feed([
        ("A", "Politics", "q1", True),
        ("A", "Politics", "q2", False),
        ("B", "Science", "q3", False),
        ("C", "Health", "q4", False),
    ])
    stories = {"q1": "s1", "q2": "s1", "q3": "s2"}
    _engine.record_feed_composition(feed, user_side=1.0, kind="qtest",
                                    story_of=stories.get,
                                    already_shown={"q3", "zzz"},
                                    blindspot_topics=("Health",))

    def d(key):
        return _counters().get(key, 0) - before.get(key, 0)

    # HHI: shares 2/4, 1/4, 1/4 → 0.25 + 0.0625 + 0.0625 = 0.375 → 3750 bp.
    assert d("feed_hhi_bp_total|qtest") == 3750
    assert d("feed_topics_total|qtest") == 3
    assert d("feed_story_dup_total|qtest") == 1        # s1 served twice → one duplicate card
    assert d("feed_repeat_total|qtest") == 1           # q3 was already shown; zzz not served
    assert d("feed_blindspot_total|qtest") == 1        # the Health card, case-insensitively
    assert d("feed_served_total|qtest") == 1           # the original counters still fire


def test_single_publisher_feed_scores_the_hhi_ceiling():
    before = _counters()
    _engine.record_feed_composition(_feed([("A", "T", "x1", False), ("A", "T", "x2", False)]),
                                    user_side=0.0, kind="qhhi")
    assert _counters().get("feed_hhi_bp_total|qhhi", 0) - before.get("feed_hhi_bp_total|qhhi", 0) == 10000


def test_the_extension_is_optional_for_older_callers():
    # No new kwargs, malformed cards, empty feed — none of it raises, exactly as before.
    _engine.record_feed_composition(_feed([("A", "T", "x", False)]), user_side=1.0, kind="qold")
    _engine.record_feed_composition([{"article": None}], user_side=1.0, kind="qold")
    _engine.record_feed_composition([], user_side=1.0, kind="qold")


def test_serving_wires_the_same_inputs_the_ranking_consumed():
    import ast
    src = (ROOT / "examples" / "api_server.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_serialize_recommendations")
    call = next(c for c in ast.walk(fn) if isinstance(c, ast.Call)
                and getattr(c.func, "id", "") == "record_feed_composition")
    kw = {k.arg for k in call.keywords}
    assert {"story_of", "already_shown", "blindspot_topics"} <= kw, (
        "the quality metrics must measure the same story/repetition/blind-spot inputs the "
        "ranking mechanisms consumed — separate inputs drift, and drifted metrics lie.")
