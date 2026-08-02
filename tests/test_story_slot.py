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


# --------------------------------------------------------------------------- #
# P2 (2026-08-02): opposite-viewpoint siblings rank ahead of same-side ones.
#
# Verified in production before this existed: selection was (publishedAt, url), so with a same-side
# sibling published 2.4h after an opposite-side one, the slot served the same side the reader had
# just read. "Opposite" is relative to the ANCHOR — the article just read — by the catalog's own
# +-0.5 buckets; recency orders WITHIN each viewpoint group; and with no opposite-side sibling the
# key degenerates to the original, which the twelve pre-P2 tests above continue to pin unchanged.
# --------------------------------------------------------------------------- #

LEAN_STORY = "Tribunal overturns the coastal levy accord decision"


def _lean_stack(tmp_path, monkeypatch, members):
    """The real serving stack around ONE lean-controlled cluster.

    ``members``: ``(url, publisher, lean, days_ago)``; ``members[0]`` is the ANCHOR the reader
    read. Titles share the cluster vocabulary; filler is token-disjoint and sized so no sibling is
    served organically. The reader's own diet is LEFT (NPR filler reads) — deliberately, so a test
    can tell anchor-relative opposition from reader-relative opposition."""
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "5")
    monkeypatch.setenv("RWE_SEED", "0")
    monkeypatch.setenv("RWE_STORY_SLOT", "1")
    st = store_mod.Store(f"sqlite:///{tmp_path / 'lean.db'}")
    suffixes = ["", " again", " today", " briefing", " update"]
    for i, (url, pub, lean, days_ago) in enumerate(members):
        _feed(st, url, pub, LEAN_STORY + suffixes[i % len(suffixes)], days_ago=days_ago, lean=lean)
    pubs = [("AP", 0.0), ("Reuters", 0.0), ("NPR", -1.0), ("BBC News", 0.0),
            ("The Hill", 0.5), ("Newsmax", 1.5)]
    for k in range(120):
        pub, lean = pubs[k % len(pubs)]
        _feed(st, f"https://{pub.split()[0].lower()}{k % len(pubs)}.example.com/x/{k}", pub,
              f"filing{k} memo{k} briefing{k} notice{k} dossier{k}",
              days_ago=1.0 + (k % 5) * 0.1, lean=lean)
    uid = st.upsert_user_by_identity("dev", "lean-reader").id
    _read(st, uid, members[0][0], members[0][1], LEAN_STORY)
    for k in (2, 8, 14, 20):                        # NPR filler -> a LEFT diet
        _read(st, uid, f"https://npr2.example.com/x/{k}", "NPR",
              f"filing{k} memo{k} briefing{k} notice{k} dossier{k}")
    er._INDEX_CACHE.update(key=None, index=None)

    import api_server as engine
    import feed_source
    ns = SimpleNamespace(profile=None, npz=None, qbias=None, register_csv=None, emotion_csv=None,
                         behaviors=None, lean_tau=None, domain=None, n_users=None,
                         max_items=None, seed=0)
    feed_csv = feed_source.prepare(st)
    assert feed_csv
    monkeypatch.setenv("RWE_QBIAS", feed_csv)
    monkeypatch.setenv("RWE_PROFILE", "qbias")
    be = engine.Backend(engine.resolve_profile(ns))
    be.attach_url_resolver(feed_source.load_url_map(feed_csv))
    return personalize.Personalizer(be, st, persist=False), uid


def _top(pers, uid):
    feed = pers.recommendations(uid)
    card = feed[0]
    assert card["strategy"] == "story", f"the slot must fire; top was {card['strategy']}"
    return er._canon(str(card["article"]["url"]))


def test_opposite_view_sibling_beats_a_newer_same_side_one(tmp_path, monkeypatch):
    """The production case that motivated P2, now inverted: the LEFT reader read the LEFT anchor;
    a LEFT sibling is the newest coverage but a RIGHT one exists — the RIGHT one must win."""
    pers, uid = _lean_stack(tmp_path, monkeypatch, [
        ("https://cnn2.example.com/story/levy", "CNN", -1.0, 1.2),        # anchor, read
        ("https://fox2.example.com/story/levy", "Fox News", 1.5, 1.1),    # opposite, OLDER
        ("https://guardian2.example.com/story/levy", "The Guardian", -1.5, 1.0),  # same side, newest
    ])
    assert _top(pers, uid) == er._canon("https://fox2.example.com/story/levy")


def test_opposition_is_anchor_relative_not_reader_relative(tmp_path, monkeypatch):
    """The discriminator. The reader's DIET is left, but the article just read is RIGHT — so the
    other side of *this story* is LEFT. A reader-relative implementation (or the old plain-recency
    key) would pick the newer RIGHT sibling; anchor-relative picks the older LEFT one."""
    pers, uid = _lean_stack(tmp_path, monkeypatch, [
        ("https://fox3.example.com/story/levy", "Fox News", 1.5, 1.2),    # anchor, read (RIGHT)
        ("https://cnn3.example.com/story/levy", "CNN", -1.0, 1.1),        # opposite-of-anchor, older
        ("https://newsmax3.example.com/story/levy", "Newsmax", 1.5, 1.0), # same-as-anchor, newest
    ])
    assert _top(pers, uid) == er._canon("https://cnn3.example.com/story/levy")


def test_same_side_by_a_different_number_is_not_opposite(tmp_path, monkeypatch):
    """-1.5 vs an anchor of -1.0 differs numerically and opposes nothing. The newest sibling's
    lean EQUALS the anchor's — deliberately: a mutant that reads "opposite" as "any different
    number" promotes only the older -1.5 and picks it, while the true bucket rule sees one
    viewpoint group and keeps the pre-P2 behaviour verbatim: newest coverage wins."""
    pers, uid = _lean_stack(tmp_path, monkeypatch, [
        ("https://cnn4.example.com/story/levy", "CNN", -1.0, 1.2),          # anchor, read
        ("https://guardian4.example.com/story/levy", "The Guardian", -1.5, 1.1),  # same side, older
        ("https://msnbc4.example.com/story/levy", "MSNBC", -1.0, 1.0),      # same side, NEWEST
    ])
    assert _top(pers, uid) == er._canon("https://msnbc4.example.com/story/levy")


def test_recency_breaks_ties_inside_the_opposite_group(tmp_path, monkeypatch):
    pers, uid = _lean_stack(tmp_path, monkeypatch, [
        ("https://cnn5.example.com/story/levy", "CNN", -1.0, 1.2),          # anchor, read
        ("https://fox5.example.com/story/levy", "Fox News", 1.5, 1.1),      # opposite, older
        ("https://nypost5.example.com/story/levy", "New York Post", 1.0, 1.0),  # opposite, NEWEST
    ])
    assert _top(pers, uid) == er._canon("https://nypost5.example.com/story/levy")


def test_opposing_leans_is_bucketed_and_licenses_no_claim_from_unrated():
    """The predicate itself (L2.2 at the slot). Unit-level ON PURPOSE: an unrated article never
    even reaches selection end-to-end — the corpus export drops lean-less articles, so the
    `col is None` gate excludes them upstream — which made an integration assertion here vacuous
    (the first draft of this test passed against a mutant that counted unrated as opposite).
    The predicate is the one place the rule lives, so it is pinned directly."""
    assert personalize._opposing_leans(-1.0, 1.5) and personalize._opposing_leans(1.5, -1.0)
    assert personalize._opposing_leans(-0.5, 0.5), "the buckets' own boundary is inclusive"
    assert not personalize._opposing_leans(-1.0, -1.5), "same side, different number"
    assert not personalize._opposing_leans(-1.0, 0.4), "centre opposes nothing"
    assert not personalize._opposing_leans(0.0, 0.0)
    assert not personalize._opposing_leans(None, 1.5), "unrated anchor licenses no claim"
    assert not personalize._opposing_leans(-1.0, None), "unrated sibling licenses no claim"
    assert not personalize._opposing_leans(float("nan"), 1.5), "NaN survives float() and must fail"
    assert not personalize._opposing_leans("garbage", 1.5)


# --------------------------------------------------------------------------- #
# P1 (2026-08-02): the flag must be deployable. It existed only in the Colab notebooks; the
# production compose `environment:` block is an explicit allowlist with no env_file, so
# RWE_STORY_SLOT in deploy/.env could never reach the api container — the feature was
# structurally off in production regardless of operator intent.
# --------------------------------------------------------------------------- #


def test_the_flag_is_wired_through_the_production_compose():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text()
    api_block = compose.split("\n  api:", 1)[1].split("\n  web:", 1)[0]
    assert "RWE_STORY_SLOT" in api_block, \
        "the api service must pass RWE_STORY_SLOT through, or deploy/.env cannot enable the slot"
    assert "${RWE_STORY_SLOT:-0}" in api_block, \
        "the compose default must preserve OFF — enabling is an explicit deploy/.env decision"


# --------------------------------------------------------------------------- #
# Stage instrumentation (2026-08-02) — OBSERVATIONAL ONLY.
#
# The timers exist to answer "which stage dominates the post-read feed latency" with measurement
# rather than inference. They are on the critical path of every recommendation, so the property
# that matters is that they cannot change or break what is served: a metrics backend that throws
# must not cost a reader their feed.
# --------------------------------------------------------------------------- #


def test_stage_timers_record_without_changing_the_feed(stack, monkeypatch):
    import obs_metrics
    obs_metrics.metrics().reset()
    st, pers, uid = stack

    before = _urls(pers.recommendations(uid))               # cold: builds the model
    timers = set(obs_metrics.metrics().snapshot()["timers"])
    assert {"rec_serve_model_ms", "rec_serve_rank_serialize_ms"} <= timers, sorted(timers)
    assert {"rec_cache_key_ms", "rec_build_augment_ms", "rec_build_recommenders_ms",
            "rec_build_population_ms"} <= timers, sorted(timers)

    # A cache HIT is counted separately from a MISS — that split is the whole cold/warm story, so
    # the report can't attribute a warm serve to a rebuild (or the reverse).
    after = _urls(pers.recommendations(uid))                # warm: same version -> cache hit
    counters = obs_metrics.metrics().snapshot()["counters"]
    assert counters.get("rec_model_cache_miss_total") == 1, counters
    assert counters.get("rec_model_cache_hit_total") == 1, counters
    assert after == before, "timers must not perturb the served feed"


def test_stage_lines_are_reachable_without_a_configured_root_logger():
    """Under uvicorn's default logging config the ROOT logger has no handler and no level, so a
    bare ``ih.*`` logger is not enabled at INFO and no handler can see it — the breakdown lines
    would be silently dropped in production while passing every test (pytest configures root).
    The invariant that survives that: the logger owns its handler and its own level."""
    import logging
    assert personalize._logger.handlers, "ih.personalize must not depend on root being configured"
    assert personalize._logger.level <= logging.INFO
    assert not personalize._logger.propagate, "own handler + propagate -> duplicate lines"


def test_the_report_persist_stage_is_timed_on_the_production_path(stack):
    """``persist=True`` is production's setting (and the fixture's opposite), so the report write —
    the one stage carrying an *unmeasured* "cheap next to the compute above" comment — would
    otherwise never appear in a timing breakdown taken from the tests."""
    import obs_metrics
    st, pers, uid = stack
    live = personalize.Personalizer(pers.backend, st, persist=True)
    obs_metrics.metrics().reset()

    assert _urls(live.recommendations(uid)) == _urls(pers.recommendations(uid))
    assert "rec_build_persist_report_ms" in obs_metrics.metrics().snapshot()["timers"]


def test_a_failing_metrics_backend_never_costs_a_reader_their_feed(stack, monkeypatch):
    st, pers, uid = stack
    expected = _urls(pers.recommendations(uid))

    def boom(*a, **kw):
        raise RuntimeError("metrics backend down")
    monkeypatch.setattr(personalize.obs_metrics, "observe", boom)
    monkeypatch.setattr(personalize.obs_metrics, "incr", boom)
    monkeypatch.setattr(personalize._logger, "info", boom)
    pers._cache.clear()                                   # force the instrumented cold path too

    assert _urls(pers.recommendations(uid)) == expected
