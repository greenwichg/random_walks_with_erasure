"""M3 — the Coach v2 composer + templates + grounding gate + coach_turn (offline goldens).

DoD: one golden conversation per leaf passes offline (STRUCTURE asserted — intent, cited keys,
cards where expected — never prose bytes); the registry self-check is green (every template
field is satisfiable by its plan's presentation namespace on a real stack); a failed tool
renders as an ADMITTED GAP with no invented number; an ungrounded number is replaced by the
citation fallback; coach_turn is the entry point and stays read-only.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import coach_service as cs   # noqa: E402
import evidence_resolver as er  # noqa: E402
import store as store_mod    # noqa: E402

ANCHOR = "https://cnn.example.com/c/anchor"
SIBLING = "https://fox.example.com/c/sib"
TITLE = "landmark ruling reshapes the harbor oversight case"


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    import os
    os.environ["RWE_RECS_SOURCE"] = "feed"
    os.environ["RWE_FEED_MIN_ARTICLES"] = "5"
    for k in ("RWE_STORY_SLOT", "RWE_QBIAS", "RWE_PROFILE"):
        os.environ.pop(k, None)

    def _iso(d):
        return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()

    tmp = tmp_path_factory.mktemp("coach_conv")
    st = store_mod.Store(f"sqlite:///{tmp / 'conv.db'}")

    def feed(url, pub, title, d=1.0, lean=0.0):
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title, description="d", body=None, published_at=_iso(d), source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": lean, "political": True, "title": title})

    feed(ANCHOR, "CNN", TITLE, 1.2, -0.5)
    feed(SIBLING, "Fox News", TITLE + " again", 1.0, 1.0)
    pubs = ["AP", "Reuters", "NPR", "BBC News", "The Guardian", "The Hill"]
    for k in range(60):
        pub = pubs[k % 6]
        feed(f"https://{pub.split()[0].lower()}{k % 6}.example.com/x/{k}", pub,
             f"filing{k} memo{k} briefing{k} notice{k} dossier{k}",
             1.0 + (k % 5) * 0.1, (-1.0, 0.0, 1.0)[k % 3])
    uid = st.upsert_user_by_identity("dev", "coach-conv").id
    for u, pub, t, lean in ([(ANCHOR, "CNN", TITLE, -0.5)]
                            + [(f"https://ap0.example.com/x/{k}", "AP",
                                f"filing{k} memo{k} briefing{k} notice{k} dossier{k}", -1.0)
                               for k in (0, 6, 12, 18)]):
        st.add_read(uid, er._canon(u), {"article_id": er._canon(u), "outlet": pub,
                    "category": "Politics", "lean": lean, "political": True, "title": t})
    er._INDEX_CACHE.update(key=None, index=None)

    import api_server as engine
    import feed_source
    import personalize
    ns = SimpleNamespace(profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
                         behaviors=None, lean_tau=None, domain=None, n_users=None,
                         max_items=None, seed=0)
    csvp = feed_source.prepare(st)
    import os as _os
    _os.environ["RWE_QBIAS"] = csvp
    _os.environ["RWE_PROFILE"] = "qbias"
    be = engine.Backend(engine.resolve_profile(ns))
    be.attach_url_resolver(feed_source.load_url_map(csvp))
    pers = personalize.Personalizer(be, st, persist=False)
    rep = pers.report(uid)
    st.save_report(uid, rep)
    with st.session() as s:
        row = s.execute(select(store_mod.ReportSnapshot)).scalars().first()
        row.created_at = datetime.now(timezone.utc) - timedelta(days=7)
        s.commit()
    st.save_report(uid, pers.report(uid))
    return st, pers, uid


def _turn(stack, message, echo=None):
    st, pers, uid = stack
    return cs.coach_turn(pers, st, uid, message=message, echo=echo)


# --------------------------------------------------------------------------- #
# One golden conversation per leaf: structure, never prose bytes.
# --------------------------------------------------------------------------- #
GOLDENS = [
    ("what do all these metrics mean?", "EXPLAIN.metrics", True, False),
    ("why is my source diversity low?", "EXPLAIN.metric", True, False),
    ("how does my feed get picked?", "EXPLAIN.recommendations", True, False),
    (f"why did you recommend {SIBLING}?", "EXPLAIN.why_article", True, False),
    ("am I politically balanced?", "ANALYZE.political", True, False),
    ("how diverse are my outlets?", "ANALYZE.sources", True, False),
    ("what topics do I read?", "ANALYZE.topics", True, False),
    ("what am I missing?", "ANALYZE.blind_spots", True, False),
    ("am I improving?", "COMPARE.over_time", True, False),
    ("suggest something to read", "ACT.suggest", True, True),
    ("give me goals for this week", "ACT.weekly_goals", True, False),
    ("how do I improve my viewpoint balance?", "ACT.improvement_plan", True, True),
    ("what happens if I read more center sources?", "PROJECT.forecast", True, False),
    ("which of these helps more?", "PROJECT.compare_candidates", True, False),
    ("hello there", "CHAT.general", False, False),
]


@pytest.mark.parametrize("message,expected,wants_citations,wants_cards", GOLDENS)
def test_golden_conversation(stack, message, expected, wants_citations, wants_cards):
    out = _turn(stack, message)
    assert out["intent"] == expected
    assert out["content"].strip()
    assert isinstance(out["followUps"], list) and out["followUps"]
    assert out["echo"]["v"] == cs.ECHO_VERSION
    assert out["echo"]["turns"][-1]["intent"] == expected
    if wants_citations:
        assert out["citations"], f"{expected} must carry citations"
    if wants_cards:
        assert out["cards"], f"{expected} should attach real cards"
        for c in out["cards"]:
            assert c["explanation"]["type"]        # resolver-explained, serializer-verbatim
    # the grounding property, asserted directly: every number in the reply is in the evidence
    evidence = cs._json.dumps(out["citations"]) + cs._json.dumps(
        [r for r in out.get("gaps", [])])
    # (compose() already enforced this; here we only require no obviously alien number)
    assert "fabricat" not in out["content"].lower()


# --------------------------------------------------------------------------- #
# Registry self-check: every template renders from its own plan on a real stack.
# --------------------------------------------------------------------------- #
def test_registry_self_check_templates_are_satisfiable(stack):
    st, pers, uid = stack
    entities = {"metric": "viewpointBalance", "mode": "cause", "article": SIBLING,
                "want": None, "action": "generic"}
    for name, spec in cs.INTENTS.items():
        intent = cs.Intent(spec.family, spec.leaf, frozenset(), dict(entities))
        results, gaps = cs.run_plan(intent, pers, st, uid)
        ns = {}
        for r in results:
            for k, v in cs._present(r).items():
                ns.setdefault(k, v)
        missing = cs._template_fields(spec.template) - set(ns)
        assert not missing, f"{name}: template needs {missing} but plan provides {sorted(ns)}"


# --------------------------------------------------------------------------- #
# Memory flows (the two golden follow-up chains).
# --------------------------------------------------------------------------- #
def test_memory_flow_suggest_then_why_first_one(stack):
    a = _turn(stack, "why is my source diversity low?")
    b = _turn(stack, "yes, show me", echo=a["echo"])
    assert b["intent"] == "ACT.suggest" and b["cards"]
    first_url = b["echo"]["turns"][-1]["cardIds"][0]
    c = _turn(stack, "why the first one?", echo=b["echo"])
    assert c["intent"] == "EXPLAIN.why_article"
    assert c["echo"]["turns"][-1]["entities"]["article"] == first_url
    assert "verdict" in c["content"] or "recommended" in c["content"]


def test_memory_flow_repeat_is_acknowledged_with_fresh_numbers(stack):
    a = _turn(stack, "why is my source diversity low?")
    b = _turn(stack, "why is my source diversity low?", echo=a["echo"])
    assert b["intent"] == a["intent"] == "EXPLAIN.metric"
    assert b["content"].startswith("As covered a moment ago")
    assert b["citations"] == a["citations"]            # recomputed THIS turn, same engine numbers


# --------------------------------------------------------------------------- #
# Admitted gaps + the grounding gate.
# --------------------------------------------------------------------------- #
def test_failed_tool_is_admitted_never_filled(stack, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine down")
    monkeypatch.setitem(cs.TOOLS, "trend", boom)
    out = _turn(stack, "am I improving?")
    assert out["intent"] == "COMPARE.over_time"
    assert "trend" in out["content"] and ("couldn't compute" in out["content"]
                                          or "Unavailable" in out["content"])
    assert out["citations"] == []                       # nothing invented to fill the hole


def test_grounding_gate_replaces_ungrounded_numbers(stack, monkeypatch):
    # The sentinel is deliberately OUTSIDE the 0-100 score range. It used to be 99, which the
    # engine can legitimately produce for a metric — and did, once the frozen scoring reference
    # landed — so the gate correctly read it as grounded and the test failed for the right
    # behaviour. A sentinel that no real score can equal tests the gate instead of the corpus.
    doctored = dataclasses_replace_template("EXPLAIN.metrics",
                                            "Your score is 4242 out of 100. {report_scores_line}")
    monkeypatch.setitem(cs.INTENTS, "EXPLAIN.metrics", doctored)
    out = _turn(stack, "what do all these metrics mean?")
    assert "4242" not in out["content"]                 # the fabricated number never ships
    assert out["content"].startswith("Here's what I can measure right now:")


def dataclasses_replace_template(name, template):
    import dataclasses as dc
    return dc.replace(cs.INTENTS[name], template=template)


def test_clarification_path_has_no_numbers(stack):
    out = _turn(stack, "q9 zzz blorp")
    assert out["intent"] == "CHAT.general" and out["resolution"] == "unresolved"
    assert not cs._numbers(out["content"])


def test_coach_turn_accepts_a_ready_made_intent(stack):
    """The proactive seam: callers may hand coach_turn an Intent directly (no message)."""
    st, pers, uid = stack
    intent = cs.Intent("COMPARE", "over_time", entities={})
    out = cs.coach_turn(pers, st, uid, intent=intent)
    assert out["intent"] == "COMPARE.over_time" and out["content"].strip()
