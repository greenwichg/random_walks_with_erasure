"""Tier-2 candidate sources (docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md): the story source proper,
the emerging-story source, blind-spot v2, the plan rebalance beneath them, and the cohort/shadow
harness that gates them.

Four families of guarantee, in the Tier-1 tradition (dark by default, byte-identical until an
operator opts in, explain parity, budgets never silently move):

* **Plan rebalance** — extras take their budget from the discovery/adaptive slices only, the
  rwe-b bridge floor and the feed total are preserved exactly, and impossible asks shrink the
  EXTRA, never an RWE slice below 1.
* **Story source** — flag off is byte-identical; on, story cards are validated unread siblings
  from other publishers, front-positioned, feed length unchanged, and the one-card slot is
  superseded (never double-served).
* **Emerging source** — only multi-publisher, recent, reader-unread stories qualify; one card
  per story.
* **Cohorts/shadow** — deterministic hash arms, recorded once, control = the exact status-quo
  feed, shadow = metrics under ``shadow:*`` with the served feed untouched.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import api_server as engine      # noqa: E402
import evidence_resolver as er   # noqa: E402
import obs_metrics               # noqa: E402
import personalize               # noqa: E402
import rec_experiments as rx     # noqa: E402
import story_service             # noqa: E402
import store as store_mod        # noqa: E402


# ------------------------------------------------------------------ #
# _plan_with_extras — the budget arithmetic every source stands on
# ------------------------------------------------------------------ #
def test_plan_with_extras_identity_without_extras():
    plan = engine.DEFAULT_BLEND_PLAN
    assert engine._plan_with_extras(plan, []) == plan
    assert engine._plan_with_extras(plan, [("story", 0)]) == plan


def test_plan_with_extras_taxes_discovery_and_adaptive_never_the_bridge():
    plan = engine._plan_with_extras(engine.DEFAULT_BLEND_PLAN, [("story", 2)])
    assert plan == (("rwe-b", 6), ("rwe-d", 3), ("adaptive", 3), ("story", 2))
    assert sum(k for _, k in plan) == sum(k for _, k in engine.DEFAULT_BLEND_PLAN)


def test_plan_with_extras_orders_extras_and_preserves_total():
    plan = engine._plan_with_extras(engine.DEFAULT_BLEND_PLAN,
                                    [("story", 2), ("emerging", 1), ("blindspot", 1)])
    assert plan == (("rwe-b", 6), ("rwe-d", 2), ("adaptive", 2),
                    ("story", 2), ("emerging", 1), ("blindspot", 1))
    assert sum(k for _, k in plan) == 14


def test_plan_with_extras_shrinks_the_extras_when_floors_bind():
    # 4+4 non-bridge slots can give at most 6 (each slice floors at 1): an ask of 8 grants 6,
    # split first-come; the rwe-b budget never moves and no slice reaches 0.
    plan = engine._plan_with_extras(engine.DEFAULT_BLEND_PLAN, [("story", 8), ("emerging", 2)])
    counts = dict(plan)
    assert counts["rwe-b"] == 6 and counts["rwe-d"] == 1 and counts["adaptive"] == 1
    assert counts["story"] == 6 and "emerging" not in counts
    assert sum(counts.values()) == 14


def test_plan_with_extras_respects_openness_bridge_budget():
    # A reader with a moved openness slider has a different rwe-b budget — the rebalance must
    # tax whatever the OTHER slices actually hold, not assume the default split.
    base = engine.blend_plan_for({"openness": 100})
    plan = engine._plan_with_extras(base, [("story", 2)])
    assert dict(plan)["rwe-b"] == dict(base)["rwe-b"]
    assert sum(k for _, k in plan) == sum(k for _, k in base)


# ------------------------------------------------------------------ #
# blind-spot v2 — candidate building + serving over the demo backend
# ------------------------------------------------------------------ #
@pytest.fixture(scope="module")
def backend():
    profile = engine.DatasetProfile.synthetic(n_users=200, max_items=500, seed=0)
    return engine.Backend(profile)


def test_blindspot_source_cols_admit_only_gap_topics(backend):
    mind = backend.base_corpus.mind
    u = backend.demo_user
    rep = engine.hr.user_report(backend.base_corpus.pop, mind, u)
    side = float(np.sign(rep.get("mean_lean") or 0.0))
    topic = str(np.asarray(mind.categories)[0]).strip().lower()
    cols = engine.Backend._blindspot_source_cols(mind, backend.rec, u, None, side, (topic,), 6)
    assert cols, "the corpus carries the topic, so candidates must exist"
    cats = np.asarray(mind.categories)
    assert all(str(cats[c]).strip().lower() == topic for c in cols)
    # The candidates are the discovery model's own admitted pool, filtered — order preserved.
    pool = engine.Backend._rec_cols_of(mind, backend.rec, u, "rwe-d",
                                       int(len(backend.rec.rec_ids)), None,
                                       user_side=side, blindspot=())
    assert cols == [c for c in pool if str(cats[c]).strip().lower() == topic][:6]
    assert engine.Backend._blindspot_source_cols(mind, backend.rec, u, None, side, (), 6) == []


def test_blindspot_v2_serves_its_own_slice_and_parity_holds(backend, monkeypatch):
    u = backend.demo_user
    mind = backend.base_corpus.mind
    topic = str(np.asarray(mind.categories)[0]).strip().lower()
    monkeypatch.setattr(engine.Backend, "_blindspot_topics", staticmethod(lambda rep: (topic,)))
    base = backend.recommendations(u)
    monkeypatch.setenv("RWE_REC_BLINDSPOT_SLOTS", "2")
    feed = backend.recommendations(u)
    assert len(feed) == len(base), "the feed total never grows for a source"
    bs = [r for r in feed if r["strategy"] == "blindspot"]
    assert bs and len(bs) <= 2
    # the served card carries the PRETTIFIED topic ("topic_5" → "Topic 5") — normalise to compare
    assert all(str(r["article"]["topic"]).strip().lower().replace(" ", "_") == topic for r in bs)
    assert all("measured gap" in r["reason"] for r in bs)
    # Explain replicates the sourced feed exactly (the 21a parity rule extended to sources).
    exp = backend.explain_recommendations(u)
    assert [r["articleId"] for r in exp["recommendations"]] == [r["article"]["id"] for r in feed]
    assert [r["chosenBy"] for r in exp["recommendations"]] == [r["strategy"] for r in feed]
    src = [r for r in exp["recommendations"] if r["chosenBy"] == "blindspot"]
    assert src and all(r["match"] == "source" and r["rank"] is None for r in src)
    # Single-strategy requests stay faithful single-model views — no source cards.
    single = backend.recommendations(u, "rwe-d")
    assert all(r["strategy"] == "rwe-d" for r in single)


# ------------------------------------------------------------------ #
# the real serving stack (feed corpus + Personalizer), story + emerging
# ------------------------------------------------------------------ #
def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _feed(st, url, publisher, title, days_ago=1.0, category="Politics", lean=0.0):
    st.upsert_feed_article(
        canonical_url=er._canon(url), url=url, publisher=publisher, source_publisher=publisher,
        title=title, description="d", body=None, published_at=_iso(days_ago), source_feed="f",
        scored={"article_id": er._canon(url), "outlet": publisher, "category": category,
                "lean": lean, "political": category == "Politics", "title": title})


def _read(st, uid, url, publisher, title, category="Politics"):
    st.add_read(uid, er._canon(url),
                {"article_id": er._canon(url), "outlet": publisher, "category": category,
                 "lean": 0.0, "political": category == "Politics", "title": title})


STORY_TITLE = "Landmark ruling reshapes the harbor bridge oversight case"
ANCHOR = "https://cnn.example.com/story/ruling"
SIBLING = "https://fox.example.com/story/ruling"
SIBLING2 = "https://guardian.example.com/story/ruling"

EMERGING_TITLE = "Volcanic ash cloud grounds transcontinental cargo flights"
EM_URLS = [f"https://{p}.example.com/emerging/ash"
           for p in ("apnews", "reuters0", "npr1")]
STALE_TITLE = "Archive retrospective revisits the canal treaty negotiations"
STALE_URLS = [f"https://{p}.example.com/stale/canal"
              for p in ("apnews", "reuters0", "npr1")]


def _seed_corpus(st):
    _feed(st, ANCHOR, "CNN", STORY_TITLE, days_ago=1.2)
    _feed(st, SIBLING, "Fox News", STORY_TITLE + " again", days_ago=1.0, lean=1.0)
    _feed(st, SIBLING2, "The Guardian", STORY_TITLE + " today", days_ago=1.1, lean=-1.0)
    for i, (u, p) in enumerate(zip(EM_URLS, ("AP", "Reuters", "NPR"))):
        _feed(st, u, p, EMERGING_TITLE + (" update" * i), days_ago=0.2 + i * 0.05)
    for i, (u, p) in enumerate(zip(STALE_URLS, ("AP", "Reuters", "NPR"))):
        _feed(st, u, p, STALE_TITLE + (" update" * i), days_ago=4.0 + i * 0.1)
    pubs = ["AP", "Reuters", "NPR", "BBC News", "The Guardian", "The Hill"]
    for k in range(120):
        pub = pubs[k % len(pubs)]
        _feed(st, f"https://{pub.split()[0].lower()}{k % len(pubs)}.example.com/x/{k}", pub,
              f"filing{k} memo{k} briefing{k} notice{k} dossier{k}",
              days_ago=1.0 + (k % 5) * 0.1, lean=(-1.0, 0.0, 1.0)[k % 3])


def _reader(st):
    uid = st.upsert_user_by_identity("dev", "tier2-reader").id
    _read(st, uid, ANCHOR, "CNN", STORY_TITLE)
    for k in (0, 6, 12, 18):
        _read(st, uid, f"https://ap0.example.com/x/{k}", "AP",
              f"filing{k} memo{k} briefing{k} notice{k} dossier{k}")
    return uid


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "5")
    monkeypatch.setenv("RWE_SEED", "0")
    for var in ("RWE_STORY_SLOT", "RWE_REC_STORY_SOURCE", "RWE_REC_EMERGING",
                "RWE_REC_BLINDSPOT_SLOTS", "RWE_REC_EXPERIMENT", "RWE_REC_SHADOW",
                "RWE_FEED_MAX_AGE_DAYS"):
        monkeypatch.delenv(var, raising=False)
    st = store_mod.Store(f"sqlite:///{tmp_path / 'tier2.db'}")
    _seed_corpus(st)
    uid = _reader(st)
    er._INDEX_CACHE.update(key=None, index=None)
    story_service.warm_cache(st)

    import feed_source
    ns = SimpleNamespace(profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
                         behaviors=None, lean_tau=None, domain=None, n_users=None,
                         max_items=None, seed=0)
    feed_csv = feed_source.prepare(st)
    assert feed_csv, "feed corpus must activate"
    monkeypatch.setenv("RWE_QBIAS", feed_csv)
    monkeypatch.setenv("RWE_PROFILE", "qbias")
    be = engine.Backend(engine.resolve_profile(ns))
    be.attach_url_resolver(feed_source.load_url_map(feed_csv))
    pers = personalize.Personalizer(be, st, persist=False)
    return st, pers, uid


def _urls(recs):
    return [er._canon(str((r.get("article") or {}).get("url") or "")) for r in recs]


def test_sources_off_is_byte_identical_and_pure_rwe(stack):
    st, pers, uid = stack
    a, b = pers.recommendations(uid), pers.recommendations(uid)
    assert _urls(a) == _urls(b)
    assert all(r.get("strategy") in ("rwe-b", "rwe-d", "adaptive") for r in a)


def test_story_source_serves_validated_siblings_up_front(stack, monkeypatch):
    st, pers, uid = stack
    base = pers.recommendations(uid)
    monkeypatch.setenv("RWE_REC_STORY_SOURCE", "2")
    feed = pers.recommendations(uid)
    assert len(feed) == len(base), "source budget comes out of the plan, never on top of it"
    stories = [r for r in feed if r["strategy"] == "story"]
    assert stories and len(stories) <= 2
    # front-positioned: the story cards are exactly the head of the feed
    assert [r["strategy"] for r in feed[:len(stories)]] == ["story"] * len(stories)
    # every story card is an unread different-publisher sibling of the read anchor's cluster
    sib_urls = {er._canon(SIBLING), er._canon(SIBLING2)}
    for r in stories:
        assert er._canon(str(r["article"]["url"])) in sib_urls
        assert r["article"]["publisher"] != "CNN"
    assert _urls(pers.recommendations(uid)) == _urls(feed)   # deterministic


def test_story_source_supersedes_the_one_card_slot(stack, monkeypatch):
    st, pers, uid = stack
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    monkeypatch.setenv("RWE_REC_STORY_SOURCE", "2")
    feed = pers.recommendations(uid)
    # never double-served: the story cards are the SOURCE's (siblings can appear once each)
    urls = _urls(feed)
    assert len(urls) == len(set(urls))
    assert sum(1 for r in feed if r["strategy"] == "story") <= 2


def test_story_source_explain_parity(stack, monkeypatch):
    st, pers, uid = stack
    monkeypatch.setenv("RWE_REC_STORY_SOURCE", "2")
    monkeypatch.setenv("RWE_STORY_SLOT", "1")   # the slot flag is on; the source supersedes it
    served = pers.recommendations(uid)
    exp = pers.explain(uid)
    assert [r["articleId"] for r in exp["recommendations"]] == [r["article"]["id"] for r in served]
    assert [r["chosenBy"] for r in exp["recommendations"]] == [r["strategy"] for r in served]
    assert exp["storySlot"]["reason"].startswith("superseded")


def test_emerging_source_serves_recent_unread_multipublisher_stories(stack, monkeypatch):
    st, pers, uid = stack
    monkeypatch.setenv("RWE_REC_EMERGING", "1")
    feed = pers.recommendations(uid)
    em = [r for r in feed if r["strategy"] == "emerging"]
    assert em and len(em) <= 1
    em_urls = {er._canon(u) for u in EM_URLS}
    stale_urls = {er._canon(u) for u in STALE_URLS}
    got = {er._canon(str(r["article"]["url"])) for r in em}
    assert got <= em_urls, "an emerging card must come from the fresh multi-publisher story"
    assert not (got & stale_urls), "a story older than the window must never qualify"
    # the reader's OWN story (they read the anchor) is the story source's, never emerging's
    assert er._canon(SIBLING) not in got and er._canon(SIBLING2) not in got


# ------------------------------------------------------------------ #
# cohort + shadow harness
# ------------------------------------------------------------------ #
def test_cohort_hash_is_deterministic_feature_salted_and_monotone():
    assert rx.cohort_of(7, "story_source", 50) == rx.cohort_of(7, "story_source", 50)
    # pct=0 treats no one; pct=100 everyone; membership is monotone in pct
    for uidx in range(40):
        assert rx.cohort_of(uidx, "story_source", 0) == "control"
        assert rx.cohort_of(uidx, "story_source", 100) == "treatment"
        if rx.cohort_of(uidx, "story_source", 30) == "treatment":
            assert rx.cohort_of(uidx, "story_source", 60) == "treatment"
    # feature salt: the two features' treatment sets must not be the same set
    a = {u for u in range(200) if rx.cohort_of(u, "story_source", 50) == "treatment"}
    b = {u for u in range(200) if rx.cohort_of(u, "emerging", 50) == "treatment"}
    assert a != b


def test_experiment_spec_parsing(monkeypatch):
    monkeypatch.setenv("RWE_REC_EXPERIMENT", "story_source:50, emerging:10, typo:5, blindspot_v2:woof")
    assert rx.experiment_pct("story_source") == 50
    assert rx.experiment_pct("emerging") == 10
    assert rx.experiment_pct("typo") is None
    assert rx.experiment_pct("blindspot_v2") is None
    monkeypatch.setenv("RWE_REC_SHADOW", "story_source, nope ,emerging")
    assert rx.shadow_features() == ("story_source", "emerging")
    monkeypatch.delenv("RWE_REC_EXPERIMENT")
    assert rx.experiment_pct("story_source") is None


def test_assignment_recorded_once_and_immutable(tmp_path, monkeypatch):
    st = store_mod.Store(f"sqlite:///{tmp_path / 'exp.db'}")
    uid = st.upsert_user_by_identity("dev", "arm-reader").id
    monkeypatch.setenv("RWE_REC_EXPERIMENT", "story_source:100")
    assert rx.assign(st, uid, "story_source") == "treatment"
    rows = st.experiment_assignments("story_source")
    assert [(r["userId"], r["cohort"]) for r in rows] == [(uid, "treatment")]
    first = rows[0]["assignedAt"]
    # re-serving, even under a CHANGED spec, never rewrites the recorded arm
    monkeypatch.setenv("RWE_REC_EXPERIMENT", "story_source:0")
    assert rx.assign(st, uid, "story_source") == "control"
    rows = st.experiment_assignments("story_source")
    assert len(rows) == 1 and rows[0]["cohort"] == "treatment" and rows[0]["assignedAt"] == first
    # no experiment declared → None, and nothing recorded for other features
    monkeypatch.delenv("RWE_REC_EXPERIMENT")
    assert rx.assign(st, uid, "emerging") is None
    assert st.experiment_assignments("emerging") == []


def test_control_arm_serves_the_status_quo_feed(stack, monkeypatch):
    st, pers, uid = stack
    base = pers.recommendations(uid)
    monkeypatch.setenv("RWE_REC_STORY_SOURCE", "2")
    monkeypatch.setenv("RWE_REC_EXPERIMENT", "story_source:0")   # everyone control
    feed = pers.recommendations(uid)
    assert _urls(feed) == _urls(base), "control must be the exact current experience"
    assert st.experiment_assignments("story_source")[0]["cohort"] == "control"
    monkeypatch.setenv("RWE_REC_EXPERIMENT", "story_source:100")  # arm re-derived per request
    treated = pers.recommendations(uid)
    assert any(r["strategy"] == "story" for r in treated)


def test_shadow_records_the_would_be_feed_without_serving_it(stack, monkeypatch):
    st, pers, uid = stack
    monkeypatch.setenv("RWE_REC_STORY_SOURCE", "2")
    monkeypatch.setenv("RWE_REC_EXPERIMENT", "story_source:0")
    monkeypatch.setenv("RWE_REC_SHADOW", "story_source")
    before = obs_metrics.snapshot()["counters"]
    feed = pers.recommendations(uid)
    after = obs_metrics.snapshot()["counters"]
    assert all(r["strategy"] != "story" for r in feed), "shadow never serves the feature"
    key = "feed_served_total|shadow:story_source"
    assert after.get(key, 0) == before.get(key, 0) + 1
    src = "feed_source_cards_total|shadow:story_source|story"
    assert after.get(src, 0) > before.get(src, 0), "the shadow feed carried story cards"
    # the SERVED feed's own counters advanced under the ordinary kind, not the shadow kind
    assert after.get("feed_served_total|blend", 0) == before.get("feed_served_total|blend", 0) + 1
