"""Commit C6 — concrete, measured explanation facts; no generic or estimated claims.

Proves: when (and only when) the resolver's context carries the reader's REAL measured shares
(the same ``hr.user_report`` numbers the explain drawer shows), the readerFact upgrades to the
concrete form — "{topic} represents {percent}% of your recent reading" / "{percent}% of your
political reading has leaned {side}" — with the raw share in the evidence; a missing share
degrades to the plain fact (never estimated, never invented); and ``validate()`` + the RVP
evidence stage reject any percent or share the context does not license, so a shown number can
never drift from the recommendation inputs.
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import api_server as engine        # noqa: E402
import evidence_resolver as er     # noqa: E402
from rec_pipeline import evidence as rvp_evidence  # noqa: E402

TOPIC_REC = {"article": {"url": "https://ex.com/t", "id": "t", "publisher": "AP",
                         "topic": "Politics", "lean": 0.0, "political": True},
             "crossCutting": False, "strategy": "rwe-b"}
BRIDGE_REC = {"article": {"url": "https://ex.com/b", "id": "b", "publisher": "Fox News",
                          "topic": "World", "lean": 1.6, "political": True},
              "crossCutting": True, "strategy": "rwe-b"}


def _ctx(**extra):
    base = {"reads": [], "familiarity": lambda p: {"band": "familiar", "reads": 9},
            "top_topics": ["Politics"], "reader_mean_lean": -0.9}
    base.update(extra)
    return base


RICH = dict(topic_shares={"Politics": 0.42},
            lean_shares={"left": 0.74, "center": 0.16, "right": 0.10})


# --------------------------------------------------------------------------- #
# The upgrade: measured share present -> the concrete fact; absent -> the plain fact.
# --------------------------------------------------------------------------- #
def test_topic_share_upgrades_reader_fact_and_is_validated():
    ctx = _ctx(**RICH)
    out = er.resolve(TOPIC_REC, ctx, {})
    assert out["type"] == "topic_continuity"
    assert out["readerFact"] == {"key": "top_topic_share",
                                 "params": {"topic": "Politics", "percent": 42}}
    assert out["evidence"]["topicShare"] == 0.42
    assert er.validate(out, TOPIC_REC, ctx, {}) == []
    # the validated whole (message) is unchanged — goldens and signatures are stable
    assert out["message"] == er.resolve(TOPIC_REC, _ctx(), {})["message"]


def test_lean_share_upgrades_bridge_reader_fact_and_is_validated():
    ctx = _ctx(**RICH)
    out = er.resolve(BRIDGE_REC, ctx, {})
    assert out["type"] == "bridge"
    assert out["readerFact"] == {"key": "political_lean_left_share", "params": {"percent": 74}}
    assert out["evidence"]["readerLeanShares"] == {"left": 0.74, "center": 0.16, "right": 0.10}
    assert er.validate(out, BRIDGE_REC, ctx, {}) == []


def test_missing_shares_degrade_to_plain_facts_never_estimated():
    out_t = er.resolve(TOPIC_REC, _ctx(), {})
    assert out_t["readerFact"]["key"] == "top_topic"
    assert "topicShare" not in out_t["evidence"]
    out_b = er.resolve(BRIDGE_REC, _ctx(), {})
    assert out_b["readerFact"]["key"] == "political_lean_left"
    assert "readerLeanShares" not in out_b["evidence"]
    # a share that would round to 0% is not a claim either
    tiny = er.resolve(TOPIC_REC, _ctx(topic_shares={"Politics": 0.004}), {})
    assert tiny["readerFact"]["key"] == "top_topic"
    # malformed shares are junk, not claims
    junk = er.resolve(BRIDGE_REC, _ctx(lean_shares={"left": "high"}), {})
    assert junk["readerFact"]["key"] == "political_lean_left"


def test_balanced_reader_gets_no_lean_claim_even_with_shares():
    ctx = _ctx(reader_mean_lean=0.0, **RICH)
    out = er.resolve(BRIDGE_REC, ctx, {})
    assert "readerFact" not in out            # balanced: no reader-first claim (Commit 23 rule)
    assert er.validate(out, BRIDGE_REC, ctx, {}) == []


# --------------------------------------------------------------------------- #
# Drift protection: validate() + the RVP evidence stage reject unlicensed numbers.
# --------------------------------------------------------------------------- #
def test_validate_rejects_forged_or_unlicensed_percents():
    ctx = _ctx(**RICH)
    good_t = er.resolve(TOPIC_REC, ctx, {})
    good_b = er.resolve(BRIDGE_REC, ctx, {})

    forged = dict(good_t, readerFact={"key": "top_topic_share",
                                      "params": {"topic": "Politics", "percent": 99}})
    assert any("percent does not match" in f for f in er.validate(forged, TOPIC_REC, ctx, {}))

    forged = dict(good_b, readerFact={"key": "political_lean_left_share",
                                      "params": {"percent": 9}})
    assert any("percent does not match" in f for f in er.validate(forged, BRIDGE_REC, ctx, {}))

    # a share claim against a context that carries no shares is an over-claim
    assert any("carries no such share" in f
               for f in er.validate(good_t, TOPIC_REC, _ctx(), {}))
    assert any("carries no shares" in f
               for f in er.validate(good_b, BRIDGE_REC, _ctx(), {}))

    # the wrong side never validates
    wrong_side = dict(good_b, readerFact={"key": "political_lean_right_share",
                                          "params": {"percent": 10}})
    assert er.validate(wrong_side, BRIDGE_REC, ctx, {}) != []


def test_rvp_evidence_stage_rejects_invented_shares():
    ctx = _ctx(**RICH)
    good_t = er.resolve(TOPIC_REC, ctx, {})
    good_b = er.resolve(BRIDGE_REC, ctx, {})
    assert rvp_evidence.evidence_subset_of_context(good_t, TOPIC_REC, ctx, {}) == []
    assert rvp_evidence.evidence_subset_of_context(good_b, BRIDGE_REC, ctx, {}) == []

    tampered = dict(good_t, evidence=dict(good_t["evidence"], topicShare=0.9))
    assert any("not the reader's measured share" in f
               for f in rvp_evidence.evidence_subset_of_context(tampered, TOPIC_REC, ctx, {}))

    tampered = dict(good_b, evidence=dict(good_b["evidence"],
                                          readerLeanShares={"left": 0.9, "center": 0.05,
                                                            "right": 0.05}))
    assert any("not the reader's measured shares" in f
               for f in rvp_evidence.evidence_subset_of_context(tampered, BRIDGE_REC, ctx, {}))


# --------------------------------------------------------------------------- #
# The context builders feed the report's OWN numbers (parity with the drawer).
# --------------------------------------------------------------------------- #
def test_context_share_extractors_from_user_report():
    rep = {"top_categories": [("Politics", 0.42), ("business", 0.21), ("", 0.1),
                              ("general", 0.05)],
           "viewpoint": (0.74, 0.16, 0.10)}
    shares = engine._topic_shares_of(rep)
    assert shares == {"Politics": 0.42, "Business": 0.21}     # blank/general excluded, prettified
    assert engine._lean_shares_of(rep) == {"lean_shares": {"left": 0.74, "center": 0.16,
                                                           "right": 0.10}}
    # below the report's political minimum -> NaN viewpoint -> no shares, no claim
    nan_rep = {"top_categories": [], "viewpoint": (float("nan"),) * 3}
    assert engine._lean_shares_of(nan_rep) == {}
    assert engine._lean_shares_of({}) == {}


def test_base_and_personal_context_carry_shares_from_one_source():
    """Both explanation contexts must expose the same share fields, derived from user_report —
    the single source the drawer's evidence also reads — so no surface can disagree."""
    import inspect
    for fn in (engine.Backend.explanation_context,):
        src = inspect.getsource(fn)
        assert "_topic_shares_of" in src and "_lean_shares_of" in src
    import personalize
    src = inspect.getsource(personalize.Personalizer.explanation_context)
    assert "_topic_shares_of" in src and "_lean_shares_of" in src
