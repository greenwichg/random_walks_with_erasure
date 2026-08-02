"""M2 — the Coach v2 tool layer (examples/coach_service.py TOOLS + run_plan).

DoD: every tool returns a ToolResult with >=1 citation; PARITY — each tool's citations equal
the engine surface it mirrors (report page, explanation context, live feed + resolver, explain
verdicts, analytics trends, engine blind spots, per-article projections, stored settings, story
index); a coach turn performs ZERO store writes; failed tools become admitted gaps. The module
stays unwired (pinned in test_coach_router).
"""
import dataclasses
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import coach_service as cs   # noqa: E402
import evidence_resolver as er  # noqa: E402
import settings_service as ss   # noqa: E402
import store as store_mod    # noqa: E402


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _feed(st, url, pub, title, days=1.0, lean=0.0, category="Politics"):
    st.upsert_feed_article(
        canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
        title=title, description="d", body=None, published_at=_iso(days), source_feed="f",
        scored={"article_id": er._canon(url), "outlet": pub, "category": category,
                "lean": lean, "political": category == "Politics", "title": title})


def _read(st, uid, url, pub, title, lean=0.0):
    st.add_read(uid, er._canon(url),
                {"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                 "lean": lean, "political": True, "title": title})


ANCHOR = "https://cnn.example.com/t/anchor"
SIBLING = "https://fox.example.com/t/sib"
TITLE = "landmark ruling reshapes the harbor oversight case"


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """A measured reader over a real feed corpus + one story cluster + snapshots for trends."""
    import os
    os.environ["RWE_RECS_SOURCE"] = "feed"
    os.environ["RWE_FEED_MIN_ARTICLES"] = "5"
    os.environ.pop("RWE_STORY_SLOT", None)
    os.environ.pop("RWE_QBIAS", None)
    os.environ.pop("RWE_PROFILE", None)
    tmp = tmp_path_factory.mktemp("coach_tools")
    st = store_mod.Store(f"sqlite:///{tmp / 'tools.db'}")
    _feed(st, ANCHOR, "CNN", TITLE, days=1.2, lean=-0.5)
    _feed(st, SIBLING, "Fox News", TITLE + " again", days=1.0, lean=1.0)
    pubs = ["AP", "Reuters", "NPR", "BBC News", "The Guardian", "The Hill"]
    for k in range(60):
        pub = pubs[k % 6]
        _feed(st, f"https://{pub.split()[0].lower()}{k % 6}.example.com/x/{k}", pub,
              f"filing{k} memo{k} briefing{k} notice{k} dossier{k}",
              days=1.0 + (k % 5) * 0.1, lean=(-1.0, 0.0, 1.0)[k % 3])
    uid = st.upsert_user_by_identity("dev", "coach-tools").id
    _read(st, uid, ANCHOR, "CNN", TITLE, lean=-0.5)
    for k in (0, 6, 12, 18):
        _read(st, uid, f"https://ap0.example.com/x/{k}", "AP",
              f"filing{k} memo{k} briefing{k} notice{k} dossier{k}", lean=-1.0)
    er._INDEX_CACHE.update(key=None, index=None)

    import api_server as engine
    import feed_source
    import personalize
    ns = SimpleNamespace(profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
                         behaviors=None, lean_tau=None, domain=None, n_users=None,
                         max_items=None, seed=0)
    csvp = feed_source.prepare(st)
    os.environ["RWE_QBIAS"] = csvp
    os.environ["RWE_PROFILE"] = "qbias"
    be = engine.Backend(engine.resolve_profile(ns))
    be.attach_url_resolver(feed_source.load_url_map(csvp))
    pers = personalize.Personalizer(be, st, persist=False)

    # two report snapshots a week apart -> real trend series (backdate the first)
    rep = pers.report(uid)
    st.save_report(uid, rep)
    with st.session() as s:
        row = s.execute(select(store_mod.ReportSnapshot)).scalars().first()
        row.created_at = datetime.now(timezone.utc) - timedelta(days=7)
        s.commit()
    st.save_report(uid, pers.report(uid))
    return st, pers, uid


@pytest.fixture(autouse=True)
def _warm_story_cache(stack):
    """conftest's ``_fresh_story_cache`` (autouse) clears the story cache before EVERY test, so a
    warm inside the module-scoped ``stack`` is wiped before each test body. Post Boot-P0 a cold
    request path serves ``[]`` and heals via a background refresh — in a suite that is a daemon
    racing the clears, i.e. nondeterminism. Re-warm per test: conftest autouse fixtures
    instantiate first, so this runs after the clear, peeks hit, and nothing is ever kicked."""
    import story_service
    story_service.warm_cache(stack[0])


def _run(stack, _tool, **args):
    st, pers, uid = stack
    return cs.TOOLS[_tool](pers, st, uid, {}, **args)


# --------------------------------------------------------------------------- #
# Envelope contract: every tool cites, and everything is JSON-safe.
# --------------------------------------------------------------------------- #
def test_every_tool_returns_cited_json_safe_toolresult(stack):
    st, pers, uid = stack
    calls = {"report": {}, "shares": {}, "metric": {"name": "viewpointBalance", "mode": "cause"},
             "recommendations": {}, "why_article": {"article": SIBLING}, "history": {},
             "trend": {}, "blind_spots": {}, "forecast": {}, "goals": {},
             "story_context": {"article": ANCHOR}}
    assert set(calls) == set(cs.TOOLS)
    for name, args in calls.items():
        res = cs.TOOLS[name](pers, st, uid, {}, **args)
        assert isinstance(res, cs.ToolResult) and res.tool == name
        assert len(res.citations) >= 1, f"{name} must cite"
        json.dumps({"facts": res.facts, "cards": list(res.cards),
                    "citations": [dataclasses.asdict(c) for c in res.citations],
                    "caveats": list(res.caveats), "provenance": res.provenance})  # JSON-safe
        assert res.provenance.get("reads") == 5


# --------------------------------------------------------------------------- #
# Parity: each tool == the surface it mirrors.
# --------------------------------------------------------------------------- #
def test_report_tool_matches_report_page(stack):
    st, pers, uid = stack
    res = _run(stack, "report")
    rep = pers.report(uid)
    assert res.facts["overall"] == rep["overall"]
    assert res.facts["scores"] == {m["key"]: int(m["score"]) for m in rep["metrics"]
                                   if "key" in m}
    assert res.facts["blindSpots"] == (rep.get("blindSpots") or [])


def test_shares_tool_matches_explanation_context(stack):
    st, pers, uid = stack
    res = _run(stack, "shares")
    ctx = pers.explanation_context(uid)
    assert res.facts["topicShares"] == (ctx.get("topic_shares") or {})
    assert res.facts["leanShares"] == (ctx.get("lean_shares") or {})
    assert res.facts["readerMeanLean"] == ctx.get("reader_mean_lean")


def test_metric_tool_selects_and_never_recomputes(stack):
    st, pers, uid = stack
    rep = pers.report(uid)
    scores = {m["key"]: int(m["score"]) for m in rep["metrics"] if "key" in m}
    res = _run(stack, "metric", name="sourceDiversity", mode="cause")
    assert res.facts["score"] == scores["sourceDiversity"]
    assert res.facts["drivers"]["topSources"] == (rep.get("sources") or [])[:5]
    lowest = _run(stack, "metric")                      # name=None -> lowest metric selected
    assert lowest.facts["score"] == min(scores.values())
    assert lowest.facts["lowestSelected"] is True


def test_recommendations_tool_serves_verbatim_cards(stack):
    st, pers, uid = stack
    res = _run(stack, "recommendations")
    served = pers.recommendations(uid)
    assert res.facts["served"] == len(served)
    served_urls = {er._canon(str((r.get("article") or {}).get("url") or "")) for r in served}
    for card in res.cards:
        assert er._canon(str(card["article"]["url"])) in served_urls
        assert card["explanation"]["type"]              # resolver explanation attached
    want = _run(stack, "recommendations", want="new_publisher")
    for card in want.cards:
        assert card["explanation"]["type"] == "new_publisher"


def test_why_article_tool_matches_explain_verdict(stack):
    st, pers, uid = stack
    res = _run(stack, "why_article", article=SIBLING)
    direct = pers.explain(uid, article=SIBLING)["exclusion"]
    assert res.facts["verdict"] == direct["verdict"]
    assert res.facts["byStrategy"] == (direct.get("byStrategy") or {})


def test_trend_tool_matches_analytics_series(stack):
    st, pers, uid = stack
    res = _run(stack, "trend", metric="overall")
    snaps = st.report_metric_series(uid)
    analytics = pers.backend.build_analytics(snaps, st.get_reads(uid), st.list_rec_events(uid))
    pts = analytics["healthImprovement"]
    assert res.facts["series"]["healthImprovement"]["first"] == pts[0]
    assert res.facts["series"]["healthImprovement"]["last"] == pts[-1]
    assert res.facts["series"]["healthImprovement"]["points"] == len(pts) >= 2


def test_blind_spots_tool_is_engine_output_plus_set_difference(stack):
    st, pers, uid = stack
    res = _run(stack, "blind_spots")
    rep = pers.report(uid)
    assert res.facts["blindSpots"] == (rep.get("blindSpots") or [])
    read_outlets = {str(r.get("outlet") or "") for r in st.get_reads(uid)}
    for p in res.facts["neverReadPublishers"]:
        assert p not in read_outlets


def test_forecast_tool_exposes_engine_projections_verbatim(stack):
    st, pers, uid = stack
    res = _run(stack, "forecast", k=2)
    diag = [d for d in pers.explain(uid)["recommendations"] if d.get("viewpointShift")]
    assert res.facts["estimated"] is True and "estimated" in " ".join(res.caveats)
    # strict identity: every 'after' must be SOME engine-computed shift, byte-equal
    engine_afters = [d["viewpointShift"]["after"] for d in diag]
    for cand in res.facts["candidates"]:
        assert cand["after"] in engine_afters


def test_goals_and_story_context_tools(stack):
    st, pers, uid = stack
    g = _run(stack, "goals")
    # C2b: the coach reports the NORMALISED goal (clamped/coerced to the settings contract), so it
    # matches the Settings page and dashboard — not the old raw ``.get("readingGoalMinutes", 20)``.
    assert g.facts["readingGoalMinutes"] == ss.normalize_settings(
        st.get_settings(uid))["readingGoalMinutes"]
    sc = _run(stack, "story_context", article=ANCHOR)
    story = er.story_index(st)[er._canon(ANCHOR)]
    assert sc.facts["story"]["storyId"] == story["storyId"]
    assert sc.facts["story"]["members"] == len(story["coverage"])
    none = _run(stack, "story_context", article="https://ap0.example.com/x/0")
    assert none.facts["story"] is None and none.caveats


# --------------------------------------------------------------------------- #
# C2b: the goals tool reports the NORMALISED reading goal (drift-bug fix), while coachGoals and
# hasStoredSettings keep their raw semantics.
# --------------------------------------------------------------------------- #
def _goals(st, uid):
    """Invoke the goals tool in isolation (it uses only store + uid; pers/deps are unused)."""
    return cs.TOOLS["goals"](None, st, uid, {})


def test_goals_reading_goal_is_normalized_drift_fixed():
    """The reading goal is clamped/coerced to the settings contract, so the coach agrees with the
    Settings API and dashboard. This is the ONE C2b behaviour change; every case below that is
    already in-range/valid is unchanged from the old raw read."""
    st = store_mod.Store("sqlite://")                       # in-memory
    uid = st.upsert_user_by_identity("dev", "goals-norm").id

    # (missing value) — no settings row at all -> honest default
    assert _goals(st, uid).facts["readingGoalMinutes"] == 20

    # (stored values) each save REPLACES the blob; the coach reports the NORMALISED goal.
    cases = [
        ({"readingGoalMinutes": 99999}, 600),   # out-of-range integer -> clamped high
        ({"readingGoalMinutes": -5}, 0),         # negative integer     -> clamped low
        ({"readingGoalMinutes": "45"}, 45),      # numeric string       -> coerced to int
        ({"readingGoalMinutes": "abc"}, 20),     # invalid string       -> default
        ({"readingGoalMinutes": 30}, 30),        # normal integer       -> unchanged
    ]
    for stored, expected in cases:
        st.save_settings(uid, stored)
        res = _goals(st, uid)
        assert res.facts["readingGoalMinutes"] == expected, (stored, res.facts["readingGoalMinutes"])
        # the citation must attribute the value to the normaliser, not the raw store read
        cite = [c for c in res.citations if c.key == "readingGoalMinutes"][0]
        assert cite.value == expected
        assert cite.source == "settings_service.normalize_settings"


def test_goals_coachgoals_and_hasstored_are_raw_and_unchanged():
    """C2b must NOT touch the raw-read semantics of ``coachGoals`` (an out-of-contract field the
    normaliser would drop) or ``hasStoredSettings`` (a has-any-row flag)."""
    st = store_mod.Store("sqlite://")
    uid = st.upsert_user_by_identity("dev", "goals-raw").id

    # no row -> no goals, not stored
    res = _goals(st, uid)
    assert res.facts["coachGoals"] is None
    assert res.facts["hasStoredSettings"] is False

    # a stored blob carrying an out-of-contract coachGoals list -> read RAW, verbatim (the normaliser
    # would drop it, so this proves the read still bypasses normalisation for these two fields).
    goals = ["Read 2 center outlets this week", "Follow one story to resolution"]
    st.save_settings(uid, {"coachGoals": goals, "readingGoalMinutes": 25})
    res = _goals(st, uid)
    assert res.facts["coachGoals"] == goals            # verbatim — NOT dropped
    assert res.facts["hasStoredSettings"] is True
    assert res.facts["readingGoalMinutes"] == 25       # in-range value passes through unchanged
    # coachGoals is never citable (only the reading goal is cited); its provenance stays raw settings.
    assert [c.key for c in res.citations] == ["readingGoalMinutes"]


# --------------------------------------------------------------------------- #
# D0: a coach turn is READ-ONLY — zero rows written anywhere.
# --------------------------------------------------------------------------- #
def _row_counts(st):
    counts = {}
    with st.session() as s:
        for table in store_mod.Base.metadata.sorted_tables:
            counts[table.name] = s.execute(
                select(func.count()).select_from(table)).scalar_one()
    return counts


def test_full_turns_write_nothing(stack):
    st, pers, uid = stack
    before = _row_counts(st)
    for name in ("EXPLAIN.metric", "ANALYZE.political", "ANALYZE.blind_spots",
                 "COMPARE.over_time", "ACT.suggest", "ACT.improvement_plan",
                 "PROJECT.forecast", "EXPLAIN.why_article"):
        intent = cs.Intent(*name.split("."), entities={
            "metric": "viewpointBalance", "mode": "cause", "article": SIBLING})
        results, gaps = cs.run_plan(intent, pers, st, uid)
        assert results, f"{name} produced no evidence"
    assert _row_counts(st) == before                    # not one row, anywhere


# --------------------------------------------------------------------------- #
# Executor: memo + admitted gaps.
# --------------------------------------------------------------------------- #
def test_executor_memoizes_shared_steps_within_a_turn(stack, monkeypatch):
    st, pers, uid = stack
    calls = {"n": 0}
    real = cs.TOOLS["report"]

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setitem(cs.TOOLS, "report", counting)
    intent = cs.Intent("EXPLAIN", "metric", entities={"metric": "echoChamber", "mode": "value"})
    cs.run_plan(intent, pers, st, uid)
    assert calls["n"] == 1                              # report ran once for report+metric


def test_failed_tool_becomes_an_admitted_gap_never_invention(stack, monkeypatch):
    st, pers, uid = stack

    def boom(*a, **k):
        raise RuntimeError("engine unavailable")
    monkeypatch.setitem(cs.TOOLS, "trend", boom)
    intent = cs.Intent("COMPARE", "over_time", entities={})
    results, gaps = cs.run_plan(intent, pers, st, uid)
    assert results == [] and len(gaps) == 1
    assert gaps[0]["tool"] == "trend" and "RuntimeError" in gaps[0]["reason"]


def test_why_article_without_binding_is_a_gap(stack):
    st, pers, uid = stack
    intent = cs.Intent("EXPLAIN", "why_article", entities={})
    results, gaps = cs.run_plan(intent, pers, st, uid)
    assert any(g["tool"] == "why_article" for g in gaps)


def test_coach_recommendations_honor_reader_settings(stack):
    """W1 follow-up: the coach's "live feed" tool must serve the SAME feed the recommendation
    endpoints serve for the reader's settings (openness -> RWE-B bridge budget) — explain<->served
    parity through the coach. Before the fix the tool passed no params and ignored settings; this
    reader is left-sided, so maxing openness reshapes their feed and the tool must track it.
    Snapshot + restore keeps the module-scoped fixture's settings unchanged for other tests."""
    import api_server as engine
    st, pers, uid = stack
    ids = lambda recs: [r["article"]["id"] for r in recs]
    orig = st.get_settings(uid)
    try:
        # openness maxed (bridge budget 8): the endpoint's feed for these exact settings
        st.save_settings(uid, {"politicalOpenness": 100, "recommendationStrength": 50})
        params = engine.rec_params_from_settings(st.get_settings(uid))
        served_open = ids(pers.recommendations(uid, params=params))
        r_open = _run(stack, "recommendations")
        coach_open = [c["article"]["id"] for c in r_open.cards]
        # PARITY: the coach's live cards are the top of the served feed for these settings
        assert coach_open and coach_open == served_open[:len(coach_open)]

        # default openness (bridge budget 6)
        st.save_settings(uid, {"politicalOpenness": 50, "recommendationStrength": 50})
        served_def = ids(pers.recommendations(uid, params=None))
        r_def = _run(stack, "recommendations")

        # MEANINGFUL: openness measurably changes this sided reader's served feed, and the coach
        # TRACKS it (pre-fix the tool ignored settings, so r_open == r_def — this is the regression).
        assert served_open != served_def
        assert (r_open.cards, r_open.facts["byType"]) != (r_def.cards, r_def.facts["byType"])
    finally:
        st.save_settings(uid, orig if orig is not None
                         else {"politicalOpenness": 50, "recommendationStrength": 50})
