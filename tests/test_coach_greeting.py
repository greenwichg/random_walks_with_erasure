"""M6 — the proactive greeting: deterministic trigger ladder over coach_turn (offline).

DoD coverage: greeting fallback (no settings -> today's greeting VERBATIM + weakest-metric
chips that round-trip through the router); Weekly Review firing (recap: settings + read this
week; goals: stored coachGoals fire UNGATED); suppression (settings without recent reads, and
recent reads without settings, both fall back); shadow triggers (metric-change pairs equal the
stored snapshots verbatim, story-update counts the same clusters the slot walks — and neither
can ever touch the message, even by raising); read-only (zero writes across every ladder
branch); determinism. Flag-off wire parity lives in tests/test_coach_api.py + the M0 suite.
"""
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
import store as store_mod    # noqa: E402

ANCHOR = "https://cnn.example.com/g/anchor"
SIBLING = "https://fox.example.com/g/sib"
TITLE = "sweeping verdict reshapes the estuary oversight case"


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    import os
    os.environ["RWE_RECS_SOURCE"] = "feed"
    os.environ["RWE_FEED_MIN_ARTICLES"] = "5"
    for k in ("RWE_STORY_SLOT", "RWE_QBIAS", "RWE_PROFILE"):
        os.environ.pop(k, None)

    def _iso(d):
        return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()

    tmp = tmp_path_factory.mktemp("coach_greet")
    st = store_mod.Store(f"sqlite:///{tmp / 'greet.db'}")

    def feed(url, pub, title, d=1.0, lean=0.0):
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title, description="d", body=None, published_at=_iso(d), source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": lean, "political": True, "title": title})

    feed(ANCHOR, "CNN", TITLE, 1.2, -0.5)
    feed(SIBLING, "Fox News", TITLE + " again", 1.0, 1.0)   # newer, unread -> story shadow
    pubs = ["AP", "Reuters", "NPR", "BBC News", "The Guardian", "The Hill"]
    for k in range(60):
        pub = pubs[k % 6]
        feed(f"https://{pub.split()[0].lower()}{k % 6}.example.com/g/{k}", pub,
             f"ledger{k} affidavit{k} circular{k} minutes{k} registry{k}",
             1.0 + (k % 5) * 0.1, (-1.0, 0.0, 1.0)[k % 3])

    ap_reads = [(f"https://ap0.example.com/g/{k}", "AP",
                 f"ledger{k} affidavit{k} circular{k} minutes{k} registry{k}", -1.0)
                for k in (0, 6, 12, 18)]

    def reader(account, urls, stale=False):
        uid = st.upsert_user_by_identity("dev", account).id
        for u, pub, t, lean in urls:
            st.add_read(uid, er._canon(u), {"article_id": er._canon(u), "outlet": pub,
                        "category": "Politics", "lean": lean, "political": True, "title": t})
        if stale:   # push every read outside the 7-day recap window (createdAt is the
            with st.session() as s:   # _read_at fallback when observedAt/read_at are absent)
                for row in s.scalars(select(store_mod.Read)
                                     .where(store_mod.Read.user_id == uid)).all():
                    row.created_at = datetime.now(timezone.utc) - timedelta(days=10)
                s.commit()
        return uid

    uids = {
        "default": reader("greet-default", [(ANCHOR, "CNN", TITLE, -0.5)] + ap_reads),
        "recap": reader("greet-recap", [(ANCHOR, "CNN", TITLE, -0.5)] + ap_reads),
        "stale": reader("greet-stale", [(ANCHOR, "CNN", TITLE, -0.5)] + ap_reads, stale=True),
        "goals": reader("greet-goals", [(ANCHOR, "CNN", TITLE, -0.5)] + ap_reads, stale=True),
        "nostory": reader("greet-nostory", ap_reads),        # no clustered read
    }
    st.save_settings(uids["recap"], {"readingGoalMinutes": 25})
    st.save_settings(uids["stale"], {"readingGoalMinutes": 20})
    st.save_settings(uids["goals"], {"readingGoalMinutes": 20,
                                     "coachGoals": ["Read 2 center outlets this week",
                                                    "Open one cross-perspective card"]})
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
    # two snapshots a week apart for reader A, so the metric-change shadow has a real pair
    st.save_report(uids["default"], pers.report(uids["default"]))
    with st.session() as s:
        row = s.execute(select(store_mod.ReportSnapshot)).scalars().first()
        row.created_at = datetime.now(timezone.utc) - timedelta(days=7)
        s.commit()
    st.save_report(uids["default"], pers.report(uids["default"]))
    return st, pers, uids


def _greet(stack, who):
    st, pers, uids = stack
    return cs.greeting_turn(pers, st, uids[who])


# --------------------------------------------------------------------------- #
# Greeting fallback: today's greeting VERBATIM + weakest-metric chips.
# --------------------------------------------------------------------------- #
def test_default_greeting_is_v1_verbatim_plus_chips(stack):
    st, pers, uids = stack
    g = _greet(stack, "default")
    assert g["trigger"] is None and g["turn"] is None
    v1 = pers.coach_greeting(uids["default"])[0]
    assert g["base"]["content"] == v1["content"]         # the greeting body IS v1's
    assert g["base"]["citations"] == v1["citations"]
    assert g["followUps"], "default greeting must offer chips"


def test_recent_reads_without_settings_do_not_fire(stack):
    # reader A reads daily but never touched settings — the settings row is the opt-in signal
    assert _greet(stack, "default")["trigger"] is None


def test_weakest_metric_chip_round_trips_through_the_router(stack):
    st, pers, uids = stack
    chip = _greet(stack, "default")["followUps"][0]
    routed = cs.classify(chip)
    assert routed.name == "EXPLAIN.metric" and routed.entities.get("metric")
    scores = cs.TOOLS["report"](pers, st, uids["default"], {}).facts["scores"]
    routable = {k: scores[k] for k in cs._METRIC_CHIP if k in scores}
    assert routed.entities["metric"] == min(sorted(routable), key=routable.get)


def test_every_metric_chip_binds_its_own_metric():
    for key, chip in cs._METRIC_CHIP.items():
        routed = cs.classify(chip)
        assert routed.name == "EXPLAIN.metric", f"{chip!r} routed to {routed.name}"
        assert routed.entities.get("metric") == key, f"{chip!r} bound {routed.entities}"


# --------------------------------------------------------------------------- #
# Weekly Review: firing + suppression.
# --------------------------------------------------------------------------- #
def _citation(turn, key):
    return next(c["value"] for c in turn["citations"] if c["key"] == key)


def test_weekly_recap_fires_for_settings_plus_recent_reads(stack):
    g = _greet(stack, "recap")
    assert g["trigger"] == "weekly_review_recap" and g["base"] is None
    turn = g["turn"]
    assert turn["intent"] == "COMPARE.weekly_review"
    assert turn["content"].strip() and turn["citations"]
    assert "25" in turn["content"]                       # the stored goal, cited and rendered
    assert turn["echo"]["turns"][-1]["intent"] == "COMPARE.weekly_review"
    assert turn["toolsRun"] == ["goals", "history", "trend"]


def test_stored_coach_goals_fire_even_without_recent_reads(stack):
    g = _greet(stack, "goals")                           # reads are 10 days old
    assert g["trigger"] == "weekly_review_goals"
    assert g["turn"]["intent"] == "COMPARE.weekly_review"
    assert "center outlets" in g["turn"]["content"]      # the stored goals, verbatim


def test_recap_is_suppressed_without_a_recent_read(stack):
    g = _greet(stack, "stale")                           # settings row, but reads are stale
    assert g["trigger"] is None and g["turn"] is None
    assert g["base"]["content"].startswith("Hi — I'm your Information Health guide.")
    assert g["followUps"]


def test_review_counts_only_the_windowed_reads(stack):
    # recap reader: all 5 reads are recent; goals reader: all 5 are 10 days old -> window 0
    assert _citation(_greet(stack, "recap")["turn"], "totalReads") == 5
    assert _citation(_greet(stack, "goals")["turn"], "totalReads") == 0


# --------------------------------------------------------------------------- #
# Shadow triggers: parity with the stored data; never touch the message.
# --------------------------------------------------------------------------- #
def test_metric_change_shadow_mirrors_the_snapshots_verbatim(stack):
    st, pers, uids = stack
    sh = _greet(stack, "default")["shadow"]["metricChange"]
    snaps = st.report_metric_series(uids["default"])
    assert sh["snapshots"] == len(snaps) == 2 and sh["wouldEvaluate"] is True
    assert sh["values"]["overall"] == {"prev": snaps[-2]["overall"], "last": snaps[-1]["overall"]}
    for k, pair in sh["values"].items():
        if k != "overall":
            assert pair == {"prev": snaps[-2]["metrics"][k], "last": snaps[-1]["metrics"][k]}


def test_metric_change_shadow_declines_without_a_pair(stack):
    sh = _greet(stack, "recap")["shadow"]["metricChange"]   # reader B has no snapshots
    assert sh == {"snapshots": 0, "wouldEvaluate": False}


def test_story_update_shadow_sees_the_unread_newer_sibling(stack):
    sh = _greet(stack, "default")["shadow"]["storyUpdate"]  # read CNN anchor; Fox sibling newer
    assert sh["wouldFire"] is True
    assert sh["followedStories"] >= 1 and sh["unreadNewerSiblings"] >= 1


def test_story_update_shadow_stays_quiet_without_clustered_reads(stack):
    sh = _greet(stack, "nostory")["shadow"]["storyUpdate"]
    assert sh["wouldFire"] is False and sh["storiesWithNewCoverage"] == 0


def test_shadows_never_touch_the_message_even_when_they_raise(stack, monkeypatch):
    baseline = _greet(stack, "default")
    def boom(store, uid):
        raise RuntimeError("shadow exploded")
    monkeypatch.setattr(cs, "_shadow_story_update", boom)
    monkeypatch.setattr(cs, "_shadow_metric_change", boom)
    g = _greet(stack, "default")
    assert g["base"]["content"] == baseline["base"]["content"]
    assert g["followUps"] == baseline["followUps"]
    assert g["shadow"] == {"metricChange": {"error": "RuntimeError"},
                           "storyUpdate": {"error": "RuntimeError"}}


# --------------------------------------------------------------------------- #
# Read-only + determinism.
# --------------------------------------------------------------------------- #
def test_every_ladder_branch_writes_nothing(stack):
    st, pers, uids = stack
    def counts():
        with st.session() as s:
            return {t.name: s.execute(select(func.count()).select_from(t)).scalar_one()
                    for t in store_mod.Base.metadata.sorted_tables}
    before = counts()
    for who in ("default", "recap", "stale", "goals", "nostory"):
        _greet(stack, who)
    assert counts() == before


def test_greeting_is_deterministic(stack):
    a, b = _greet(stack, "default"), _greet(stack, "default")
    assert a["trigger"] == b["trigger"] is None
    assert a["followUps"] == b["followUps"]
    assert a["base"]["content"] == b["base"]["content"]
    r1, r2 = _greet(stack, "recap"), _greet(stack, "recap")
    assert r1["turn"]["content"] == r2["turn"]["content"]
    assert r1["turn"]["citations"] == r2["turn"]["citations"]
