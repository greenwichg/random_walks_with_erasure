"""The conditional Story-Match slot (RWE_STORY_SLOT, default OFF) — personalize._apply_story_slot.

Proves, over the REAL serving stack (feed_source -> Backend -> Personalizer): the flag is off by
default and off means byte-identical feeds; on, AT MOST ONE validated story sibling is inserted at
the top with the truthful provenance ``strategy="story"``, P1-explainable and validate()-clean by
construction; an organic story_match card counts toward the cap (the slot no-ops and never removes
organic cards); displacement is semantic (the lowest card on the resolver's own priority ladder,
never a feed-order artifact); explicit-strategy requests never get the slot; and the whole
post-pass is deterministic.
"""
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import evidence_resolver as er   # noqa: E402
import personalize               # noqa: E402
import store as store_mod        # noqa: E402


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
SIBLING = "https://fox.example.com/story/ruling"          # newest coverage -> the slot's pick
SIBLING2 = "https://guardian.example.com/story/ruling"    # older sibling of the same cluster


def _seed_corpus(st):
    """Anchor + one different-publisher sibling in a real cluster, plus enough token-disjoint
    filler (production-like corpus size) that the sibling stays below every served slice —
    the measured beta condition the slot exists to convert (siblings ranked ~#91 of ~400)."""
    _feed(st, ANCHOR, "CNN", STORY_TITLE, days_ago=1.2)
    _feed(st, SIBLING, "Fox News", STORY_TITLE + " again", days_ago=1.0)
    _feed(st, SIBLING2, "The Guardian", STORY_TITLE + " today", days_ago=1.1)
    pubs = ["AP", "Reuters", "NPR", "BBC News", "The Guardian", "The Hill"]
    for k in range(120):
        pub = pubs[k % len(pubs)]
        _feed(st, f"https://{pub.split()[0].lower()}{k % len(pubs)}.example.com/x/{k}", pub,
              f"filing{k} memo{k} briefing{k} notice{k} dossier{k}",
              days_ago=1.0 + (k % 5) * 0.1, lean=(-1.0, 0.0, 1.0)[k % 3])


def _reader(st):
    uid = st.upsert_user_by_identity("dev", "slot-reader").id
    _read(st, uid, ANCHOR, "CNN", STORY_TITLE)
    for k in (0, 6, 12, 18):                       # AP filler reads: a connected, measured reader
        _read(st, uid, f"https://ap0.example.com/x/{k}", "AP",
              f"filing{k} memo{k} briefing{k} notice{k} dossier{k}")
    return uid


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "5")
    monkeypatch.setenv("RWE_SEED", "0")
    monkeypatch.delenv("RWE_STORY_SLOT", raising=False)
    monkeypatch.delenv("RWE_FEED_MAX_AGE_DAYS", raising=False)
    st = store_mod.Store(f"sqlite:///{tmp_path / 'slot.db'}")
    _seed_corpus(st)
    uid = _reader(st)
    er._INDEX_CACHE.update(key=None, index=None)

    import api_server as engine
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


def test_flag_default_off_and_parsing(monkeypatch):
    monkeypatch.delenv("RWE_STORY_SLOT", raising=False)
    assert personalize.story_slot_enabled() is False
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    assert personalize.story_slot_enabled() is True
    monkeypatch.setenv("RWE_STORY_SLOT", "off")
    assert personalize.story_slot_enabled() is False


def test_flag_off_feed_has_no_slot_and_is_deterministic(stack):
    st, pers, uid = stack
    a, b = pers.recommendations(uid), pers.recommendations(uid)
    assert _urls(a) == _urls(b)                                    # deterministic
    assert all(r.get("strategy") in ("rwe-b", "rwe-d", "adaptive") for r in a)


def test_slot_inserts_validated_sibling_at_top(stack, monkeypatch):
    st, pers, uid = stack
    base = pers.recommendations(uid)
    assert er._canon(SIBLING) not in _urls(base), "fixture must not serve the sibling organically"
    assert er._canon(SIBLING2) not in _urls(base), "fixture must not serve sibling2 organically"
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    feed = pers.recommendations(uid)
    # exactly one inserted card, at the top, same feed size
    assert len(feed) == len(base)
    card = feed[0]
    assert card["strategy"] == "story"                             # truthful provenance
    assert er._canon(str(card["article"]["url"])) == er._canon(SIBLING)
    assert sum(1 for r in feed if r.get("strategy") == "story") == 1
    # P1-explainable by construction + every validation gate green
    er._INDEX_CACHE.update(key=None, index=None)
    idx = er.story_index(st)
    ctx = pers.explanation_context(uid)
    exp = er.resolve(card, ctx, idx)
    assert exp["type"] == "story_match"
    assert er.validate(exp, card, ctx, idx) == []
    from rec_pipeline.evidence import evidence_subset_of_context
    assert evidence_subset_of_context(exp, card, ctx, idx) == []
    # ranking-stage properties: unread + a real catalog node (never fabricated)
    assert er._canon(SIBLING) not in {er._canon(ANCHOR)}
    assert card["article"]["url"]                                  # resolvable URL carried


def test_slot_displacement_is_semantic_not_positional(stack, monkeypatch):
    st, pers, uid = stack
    base = pers.recommendations(uid)
    er._INDEX_CACHE.update(key=None, index=None)
    idx = er.story_index(st)
    ctx = pers.explanation_context(uid)
    types = [er.resolve(r, ctx, idx).get("type") for r in base]
    prio = {t: i for i, t in enumerate(personalize._EXPLANATION_PRIORITY)}
    expected_drop = max(range(len(base)), key=lambda i: (
        prio.get(types[i], len(prio)),
        er._canon(str((base[i].get("article") or {}).get("url") or ""))))
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    feed = pers.recommendations(uid)
    dropped = set(_urls(base)) - set(_urls(feed))
    assert dropped == {_urls(base)[expected_drop]}                 # the ladder decides, not order
    assert types[expected_drop] != "bridge" or all(t == "bridge" for t in types)


def test_organic_story_match_counts_toward_cap(stack, monkeypatch):
    """An organically-served story_match card counts toward the one-card cap: even though a
    SECOND unread sibling of the cluster still qualifies, the slot no-ops and never removes or
    doubles the organic card."""
    st, pers, uid = stack
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    m = pers._model(uid)
    base = pers.backend._serialize_recommendations(m.corpus, m.rec, m.reader_row, None, None)
    out, diag = pers._apply_story_slot(uid, m, base)
    assert diag["fired"] is True                                   # the fixture has an opportunity
    assert er._canon(str(out[0]["article"]["url"])) == er._canon(SIBLING)   # newest coverage wins
    # a feed that already carries ONE cluster sibling (Fox) — Guardian still qualifies, yet the
    # organic story_match caps the slot
    organic = [out[0]] + base[:-1]
    out2, diag2 = pers._apply_story_slot(uid, m, organic)
    assert out2 == organic and diag2["fired"] is False
    assert "organic" in diag2["reason"]


def test_no_opportunity_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "5")
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    st = store_mod.Store(f"sqlite:///{tmp_path / 'noop.db'}")
    for k in range(12):                                            # no clusters at all
        _feed(st, f"https://ap.example.com/n/{k}", "AP",
              f"solo{k} item{k} report{k} entry{k}")
    uid = st.upsert_user_by_identity("dev", "noop-reader").id
    for k in range(5):
        _read(st, uid, f"https://ap.example.com/n/{k}", "AP",
              f"solo{k} item{k} report{k} entry{k}")
    er._INDEX_CACHE.update(key=None, index=None)
    import api_server as engine
    import feed_source
    ns = SimpleNamespace(profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
                         behaviors=None, lean_tau=None, domain=None, n_users=None,
                         max_items=None, seed=0)
    feed_csv = feed_source.prepare(st)
    monkeypatch.setenv("RWE_QBIAS", feed_csv)
    monkeypatch.setenv("RWE_PROFILE", "qbias")
    be = engine.Backend(engine.resolve_profile(ns))
    be.attach_url_resolver(feed_source.load_url_map(feed_csv))
    pers = personalize.Personalizer(be, st, persist=False)
    feed = pers.recommendations(uid)
    assert all(r.get("strategy") != "story" for r in feed)


def test_explicit_strategy_request_never_gets_the_slot(stack, monkeypatch):
    st, pers, uid = stack
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    for strat in ("rwe-b", "rwe-d", "adaptive"):
        feed = pers.recommendations(uid, strategy=strat)
        assert all(r.get("strategy") == strat for r in feed)


def test_slot_feed_is_deterministic(stack, monkeypatch):
    st, pers, uid = stack
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    assert _urls(pers.recommendations(uid)) == _urls(pers.recommendations(uid))


def test_explain_reports_the_slot_decision(stack, monkeypatch):
    st, pers, uid = stack
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    out = pers.explain(uid)
    slot = out.get("storySlot") or {}
    assert slot.get("enabled") is True and slot.get("fired") is True
    assert slot.get("inserted") == er._canon(SIBLING)
    assert (slot.get("displaced") or {}).get("explanation") in personalize._EXPLANATION_PRIORITY


def test_auditor_accounts_the_slot_card_as_served(stack, monkeypatch):
    """The story-coverage auditor must count the slot card in the TRUE served feed: Story Match
    cards served >= 1, the sibling absent from unservedSiblings, conversion 1/1, and the health
    verdict clearing to 'none' — the exact before/after a beta operator validates the flag with.
    The second (unserved) sibling of the converted story is ALREADY REPRESENTED — not a miss —
    so the missed list is empty and the opportunity bucket reads converted."""
    import audit_story_coverage as asc
    st, pers, uid = stack
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    er._INDEX_CACHE.update(key=None, index=None)
    doc = asc.full_report(st, uid)
    feed = doc["feed"] or {}
    assert feed.get("byStrategy", {}).get("story") == 1
    assert feed.get("storyMatchCards", 0) >= 1
    assert (doc["conversion"] or {}).get("ratePercent") == 100.0
    assert doc["verdict"]["code"] == "none"
    assert doc["opportunities"]["converted"] == 1
    missed_urls = {x["sibling"] for x in doc["missed"]}
    assert SIBLING not in missed_urls and er._canon(SIBLING) not in missed_urls
    # Guardian (in-graph, fresh, unserved — same story already served) = already_represented
    outcomes = {er._canon(m["url"]): m["outcome"]
                for p in doc["perRead"] for m in p["siblings"]}
    assert outcomes.get(er._canon(SIBLING2)) == "already_represented"
    assert er._canon(SIBLING2) not in missed_urls
    # Servable Story Coverage: 1 of 5 reads has a graph-eligible sibling
    assert doc["servableCoverage"] == {"withServableSibling": 1, "reads": 5, "percent": 20.0}


def _seed_and_read(st, entries, reader_reads):
    for url, pub, title, days, kw in entries:
        sc = {"article_id": er._canon(url), "outlet": pub, "category": "Politics",
              "political": True, "title": title, **kw}
        st.upsert_feed_article(canonical_url=er._canon(url), url=url, publisher=pub,
                               source_publisher=pub, title=title, description="d", body=None,
                               published_at=_iso(days), source_feed="f", scored=sc)
    uid = st.upsert_user_by_identity("dev", "bucket-reader").id
    for url, pub, title in reader_reads:
        _read(st, uid, url, pub, title)
    return uid


def _fillers(n=120):
    pubs = ["AP", "Reuters", "NPR", "BBC News", "The Guardian", "The Hill"]
    rows = []
    for k in range(n):
        pub = pubs[k % len(pubs)]
        rows.append((f"https://{pub.split()[0].lower()}{k % 6}.example.com/x/{k}", pub,
                     f"filing{k} memo{k} briefing{k} notice{k} dossier{k}",
                     1.0 + (k % 5) * 0.1, {"lean": (-1.0, 0.0, 1.0)[k % 3]}))
    return rows


def test_auditor_not_in_graph_bucket_and_servable_coverage(tmp_path, monkeypatch):
    """A sibling whose outlet never resolved a lean is in the CATALOG but not in the GRAPH: the
    opportunity lands in the notInGraph bucket (never 'ranking'), the verdict names the graph
    gap, and Servable Story Coverage excludes it while catalog-level Story Coverage counts it."""
    import audit_story_coverage as asc
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "5")
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    monkeypatch.delenv("RWE_QBIAS", raising=False)
    monkeypatch.delenv("RWE_PROFILE", raising=False)
    st = store_mod.Store(f"sqlite:///{tmp_path / 'graph.db'}")
    T = "landmark ruling reshapes the harbor oversight case"
    rows = [("https://cnn.example.com/g/anchor", "CNN", T, 1.2, {"lean": -0.5}),
            # the sibling's scored dict has NO lean -> dropped by the corpus builder (the
            # documented unknown-outlet gap) -> not_in_graph
            ("https://zvqx.example.com/g/sib", "Zvqx Chronicle", T + " again", 1.0, {})]
    uid = _seed_and_read(st, rows + _fillers(),
                         [("https://cnn.example.com/g/anchor", "CNN", T)]
                         + [(f"https://ap0.example.com/x/{k}", "AP",
                             f"filing{k} memo{k} briefing{k} notice{k} dossier{k}")
                            for k in (0, 6, 12, 18)])
    er._INDEX_CACHE.update(key=None, index=None)
    doc = asc.full_report(st, uid)
    assert doc["opportunities"] == {"converted": 0, "capSatisfied": 0, "rankedBelowCutoff": 0,
                                    "notInGraph": 1, "freshness": 0}
    assert doc["verdict"]["code"] == "graph"
    assert doc["coverageRatePercent"] == 20.0            # catalog-level: unchanged semantics
    assert doc["servableCoverage"]["withServableSibling"] == 0   # graph-level: truthfully zero
    assert doc["missed"] and doc["missed"][0]["reason"].startswith("NOT IN GRAPH")


def test_auditor_cap_bucket_vs_ranking_bucket(tmp_path, monkeypatch):
    """Two live stories, one story card: with the slot ON the unconverted story is CAP-SATISFIED
    (a servable sibling existed; the feed's one story card was taken); with the slot OFF the
    same store truthfully reports RANKING (no cap exists to blame)."""
    import audit_story_coverage as asc
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "5")
    monkeypatch.delenv("RWE_QBIAS", raising=False)
    monkeypatch.delenv("RWE_PROFILE", raising=False)
    st = store_mod.Store(f"sqlite:///{tmp_path / 'cap.db'}")
    T1 = "landmark ruling reshapes the harbor oversight case"
    T2 = "senate committee subpoenas the refinery inspection records"
    rows = [("https://cnn.example.com/c/a1", "CNN", T1, 1.2, {"lean": -0.5}),
            ("https://fox.example.com/c/s1", "Fox News", T1 + " again", 1.0, {"lean": 1.0}),
            ("https://thehill.example.com/c/a2", "The Hill", T2, 1.4, {"lean": 0.1}),
            ("https://reuters.example.com/c/s2", "Reuters", T2 + " today", 1.5, {"lean": 0.0})]
    uid = _seed_and_read(st, rows + _fillers(),
                         [("https://cnn.example.com/c/a1", "CNN", T1),
                          ("https://thehill.example.com/c/a2", "The Hill", T2)]
                         + [(f"https://ap0.example.com/x/{k}", "AP",
                             f"filing{k} memo{k} briefing{k} notice{k} dossier{k}")
                            for k in (0, 6, 12)])
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    er._INDEX_CACHE.update(key=None, index=None)
    doc = asc.full_report(st, uid)
    assert doc["opportunities"] == {"converted": 1, "capSatisfied": 1, "rankedBelowCutoff": 0,
                                    "notInGraph": 0, "freshness": 0}
    assert doc["verdict"]["code"] == "cap"
    assert "cap-satisfied" in doc["verdict"]["message"]
    # the same store with the slot OFF: no cap exists, so the truthful bucket is ranking
    monkeypatch.delenv("RWE_STORY_SLOT", raising=False)
    er._INDEX_CACHE.update(key=None, index=None)
    doc_off = asc.full_report(st, uid)
    assert doc_off["opportunities"]["capSatisfied"] == 0
    assert doc_off["opportunities"]["rankedBelowCutoff"] == 2
    assert doc_off["verdict"]["code"] == "ranking"
