"""The Story Continuation audit — examples/audit_continuation.py.

Phase 1 ships dark, so this audit is the ONLY way its resolver gets verified against real production
data. That makes the audit itself load-bearing, and two properties have to hold:

  * every gate label is reachable — a bucket that can never be attributed would silently fold its
    population into a neighbouring one and misdirect the "where do we invest next" decision;
  * attribution never disagrees with ``story_continuation.resolve`` — the audit re-walks the gates
    to name WHICH one fired, and a re-walk that has drifted from the module reports a comfortable
    number that is not true. The script self-checks this; these tests prove the self-check works.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_continuation as ac       # noqa: E402
import evidence_resolver as er        # noqa: E402
import store as store_mod             # noqa: E402

from test_story_continuation import ANCHOR, FAR, NEAR, _index, _member, _story   # noqa: E402


@pytest.fixture()
def st(tmp_path):
    """A store whose CATALOG contains the anchor. Catalog membership is what separates
    ``not_clustered`` (a real structural limit) from ``anchor_aged_out`` (a measurement artifact),
    so the default fixture has to take a side — and "still in the catalog" is the case every gate
    below the index lookup is actually about."""
    st = store_mod.Store(f"sqlite:///{tmp_path / 'audit.db'}")
    st.upsert_feed_article(
        canonical_url=er._canon(ANCHOR), url=ANCHOR, publisher="CNN", source_publisher="CNN",
        title="Harbor bridge oversight ruling lands", description="d", body=None,
        published_at="2026-08-03T09:00:00+00:00", source_feed="f",
        scored={"article_id": er._canon(ANCHOR), "outlet": "CNN", "category": "Politics",
                "lean": -0.6, "political": True, "title": "Harbor bridge oversight ruling lands"})
    return st


@pytest.fixture()
def uid(st):
    return st.upsert_user_by_identity("dev", "audit-reader").id


def _read(st, uid, url, publisher):
    st.add_read(uid, er._canon(url),
                {"article_id": er._canon(url), "outlet": publisher, "publisher": publisher,
                 "category": "Politics", "lean": 0.0, "political": True, "title": "t"})


#: One constructed story per gate, so every bucket is exercised by a case that ONLY trips that gate.
def _cases():
    ok = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    return [
        ("not_clustered", _story([_member(NEAR, "The Wall Street Journal", 0.6)]), False),
        ("cluster_untrusted", _story([_member(ANCHOR, "CNN", -0.6),
                                      _member(NEAR, "The Wall Street Journal", 0.6)],
                                     trust="low"), False),
        ("template_genre", _story([
            _member(ANCHOR, "CNN", -0.6, headline="Powerball winning numbers for Saturday"),
            _member(NEAR, "The Wall Street Journal", 0.6,
                    headline="Mega Millions jackpot winning numbers")]), False),
        ("anchor_unrated", _story([_member(ANCHOR, "CNN", None),
                                   _member(NEAR, "The Wall Street Journal", 0.6)]), False),
        ("no_unread_other_outlet", _story([_member(ANCHOR, "CNN", -0.6)]), False),
        ("no_rated_sibling", _story([_member(ANCHOR, "CNN", -0.6),
                                     _member(NEAR, "The Wall Street Journal", None)]), False),
        ("no_opposing_sibling", _story([_member(ANCHOR, "CNN", -0.6),
                                        _member(NEAR, "MSNBC", -1.2)]), False),
        ("ELIGIBLE", ok, True),
    ]


@pytest.mark.parametrize("expected,story,eligible", _cases(), ids=[c[0] for c in _cases()])
def test_every_gate_label_is_reachable(st, uid, expected, story, eligible):
    assert ac._attribute(st, uid, ANCHOR, _index(story)) == expected


@pytest.mark.parametrize("expected,story,eligible", _cases(), ids=[c[0] for c in _cases()])
def test_attribution_agrees_with_the_resolver(st, uid, expected, story, eligible):
    """The audit's verdict must be the module's verdict — the whole point of the self-check."""
    counter, drift, _ = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
    assert not drift, drift
    assert counter[expected] == 1
    assert bool(counter.get("ELIGIBLE")) is eligible


def test_a_syndicated_reprint_is_not_a_second_outlet(st, uid):
    """The audit collapses outlet identity the same way the resolver does; without that it would
    over-report the eligible rate on exactly the clusters syndication dominates."""
    story = _story([_member(ANCHOR, "Sportskeeda", -0.6), _member(NEAR, "Sportskeeda.Com", 0.6)])
    assert ac._attribute(st, uid, ANCHOR, _index(story)) == "no_unread_other_outlet"
    _, drift, _ = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
    assert not drift


def test_an_already_read_sibling_is_attributed_not_counted(st, uid):
    story = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    _read(st, uid, NEAR, "The Wall Street Journal")
    assert ac._attribute(st, uid, ANCHOR, _index(story)) == "no_unread_other_outlet"


def test_the_drift_self_check_actually_fires(st, uid, monkeypatch):
    """Prove the guard is live: if resolve and attribution ever disagree, the audit must SAY so
    rather than print a number nobody can trust."""
    story = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    monkeypatch.setattr(ac.sc, "resolve", lambda *a, **k: None)
    _, drift, _ = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
    assert drift, "attribution said ELIGIBLE while resolve said None — that must be reported"


def test_ceiling_mode_ignores_reader_state(st, uid):
    """With no reader there is nothing read and nothing stale, so the ceiling counts only the
    structural gates — which is exactly what makes it an upper bound, not a prediction."""
    story = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    _read(st, uid, NEAR, "The Wall Street Journal")
    assert ac._attribute(st, uid, ANCHOR, _index(story)) == "no_unread_other_outlet"
    assert ac._attribute(st, None, ANCHOR, _index(story)) == "ELIGIBLE"


def test_gate_list_matches_what_attribution_can_return(st, uid):
    """The printed table iterates GATES; a label attribution can return but GATES omits would be
    counted and never shown."""
    returned = {ac._attribute(st, uid, ANCHOR, _index(s)) for _n, s, _e in _cases()}
    assert returned <= set(ac.GATES)
    assert returned == set(ac.GATES) - {"stale_read", "anchor_aged_out",
                                        "index_inconsistent"}  # covered by their own tests below


def test_an_aged_out_read_is_separated_from_an_unclustered_one(st, uid):
    """The distinction the first draft got wrong. A read whose article has left the catalog says
    NOTHING about live behaviour — at prefetch the reader has just clicked something that is in the
    catalog by construction — while an article still in the catalog but in no cluster is a real
    structural limit. Collapsing them made the production headline uninterpretable."""
    gone = "https://vanished.example.com/story/last-month"
    assert ac._attribute(st, uid, gone, {}) == "anchor_aged_out"
    assert ac._attribute(st, uid, ANCHOR, {}) == "not_clustered"
    assert "anchor_aged_out" in ac._ARTIFACT and "not_clustered" not in ac._ARTIFACT


def test_at_click_time_counts_the_stale_bucket(st, uid):
    """Every historical read fails the freshness gate by construction, so the raw eligible rate over
    a backlog is ~0 however good the feature is. The predictive number adds the stale bucket back."""
    assert set(ac._AT_CLICK_TIME) == {"ELIGIBLE", "stale_read"}


def test_stale_read_is_attributed(st, uid):
    from datetime import datetime, timedelta, timezone
    story = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    _read(st, uid, ANCHOR, "CNN")
    idx, now = _index(story), datetime.now(timezone.utc)
    assert ac._attribute(st, uid, ANCHOR, idx, now=now + timedelta(hours=3)) == "ELIGIBLE"
    assert ac._attribute(st, uid, ANCHOR, idx, now=now + timedelta(hours=5)) == "stale_read"


def test_empty_index_exits_without_auditing(tmp_path, monkeypatch, capsys):
    """A cold cache must say so, not report a 0% eligible rate that reads as a product finding."""
    monkeypatch.setattr(sys, "argv", ["audit_continuation.py", "--db",
                                      f"sqlite:///{tmp_path / 'empty.db'}"])
    assert ac.main() == 2
    assert "story index is EMPTY" in capsys.readouterr().out


def test_end_to_end_over_a_real_store(tmp_path, monkeypatch, capsys):
    """The whole script, against a real clustered store and the REAL story index — the path the
    production run takes."""
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "2")
    db = f"sqlite:///{tmp_path / 'e2e.db'}"
    st = store_mod.Store(db)
    title = "Landmark ruling reshapes the harbor bridge oversight case"
    for url, pub, lean, extra in ((ANCHOR, "CNN", -0.9, ""),
                                  (NEAR, "The Wall Street Journal", 0.6, " today"),
                                  (FAR, "Breitbart", 1.2, " tonight")):
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title + extra, description="d", body=None,
            published_at="2026-08-03T09:00:00+00:00", source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": lean, "political": True, "title": title + extra})
    uid = st.upsert_user_by_identity("dev", "e2e-reader").id
    _read(st, uid, ANCHOR, "CNN")

    er._INDEX_CACHE.update(key=None, index=None)
    monkeypatch.setattr(sys, "argv", ["audit_continuation.py", "--db", db, "--inline"])
    assert ac.main() == 0                                  # 0 == no drift
    out = capsys.readouterr().out
    assert "REALIZED" in out and "eligible AT CLICK TIME" in out
    assert "DISAGREE" not in out
    er._INDEX_CACHE.update(key=None, index=None)


def test_ceiling_samples_across_all_stories_not_the_head(monkeypatch, capsys, tmp_path):
    """The index is built in story_service's RANKED order (trusted first, then publisherCount), so
    taking the first N urls samples only the biggest trusted stories. The first draft did that and
    reported zero `cluster_untrusted` over 800 production anchors — not a fact about the catalog.
    A stride must reach the tail."""
    members = [_member(f"https://p{i}.example.com/s/{i}", f"Pub {i}", -0.6 if i % 2 else 0.6)
               for i in range(100)]
    head = _story(members[:50], story_id="s-big")
    tail = _story(members[50:], story_id="s-small", trust="low")     # only reachable by a stride
    index = {**_index(head), **_index(tail)}

    monkeypatch.setattr(ac.er, "story_index", lambda *a, **k: index)
    monkeypatch.setattr(sys, "argv", ["audit_continuation.py", "--db",
                                      f"sqlite:///{tmp_path / 'c.db'}", "--ceiling", "--sample", "10"])
    ac.main()
    out = capsys.readouterr().out
    assert "cluster_untrusted" in out, "a strided sample must reach the demoted tail of the ranking"
    assert "across all" in out          # the label states the sampling, so a reader can judge it


# --------------------------------------------------------------------------- --serve
def test_resolve_reader_prefers_an_explicit_user_id(st):
    assert ac._resolve_reader(st, "nobody@example.com", 7) == 7      # --user wins outright


def test_resolve_reader_finds_the_account_by_email(st, capsys):
    uid = st.upsert_user_by_identity("google", "acct-1", email="reader@example.com").id
    assert ac._resolve_reader(st, "reader@example.com", None) == uid


def test_a_missed_email_names_the_store_it_looked_in(st, capsys):
    """The failure that sent me hunting for the wrong bug in production: 'no such user' without
    saying WHICH store was opened is indistinguishable from a typo, an empty DB, and the wrong
    container."""
    st.upsert_user_by_identity("google", "acct-2", email="someone@example.com")
    assert ac._resolve_reader(st, "typo@example.com", None) is None
    out = capsys.readouterr().out
    assert "typo@example.com" in out and "someone@example.com" in out


def test_serve_aborts_instead_of_sleeping_when_the_server_is_unreachable(st, monkeypatch, capsys):
    """A connection refused or a 401 will not heal by waiting; six 20 s sleeps would just look like
    a hang. Retrying is only for the BACKGROUND index build."""
    import time
    calls, slept = [], []
    monkeypatch.setattr(ac, "_http",
                        lambda base, path, hdr, timeout=90:
                        (calls.append(path), (0, "ConnectionRefused"))[1])
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))   # so a regression fails fast
    uid = st.upsert_user_by_identity("google", "acct-3", email="x@example.com").id
    assert ac.serve_and_probe(st, "http://127.0.0.1:9", uid) == 2
    assert sum(1 for p in calls if p == ac.WARM_PATH) == 1        # ONE attempt, then stop
    assert slept == [], "an unreachable server must not be waited on at all"
    assert "aborting the warm loop" in capsys.readouterr().out


def test_serve_counts_offers_nulls_and_errors(st, monkeypatch, capsys):
    """The probe's arithmetic, with the HTTP layer stubbed: a warm index, then one offer, one null
    and one error across three reads."""
    uid = st.upsert_user_by_identity("google", "acct-4", email="y@example.com").id
    for n, url in enumerate(("https://a.example.com/1", "https://b.example.com/2",
                             "https://c.example.com/3")):
        _read(st, uid, url, f"Pub {n}")

    offer = json.dumps({"storyId": "s1", "storyTitle": "T", "outlets": 4,
                        "anchor": {"url": "u", "publisher": "CNN", "lean": -0.6,
                                   "leanBucket": "left"},
                        "sibling": {"url": "v", "publisher": "WSJ", "headline": "h", "lean": 0.6,
                                    "leanBucket": "right", "publishedAt": "2026-08-03T00:00:00Z"},
                        "distance": 1.2, "candidateCount": 2})
    bodies = iter([offer, "null", None])          # None -> a non-200

    def fake_http(base, path, hdr, timeout=90):
        if path == "/api/metrics":
            return 200, json.dumps({"counters": {"rec_story_index_hit_total": 3},
                                    "timers": {"rec_story_index_hit_ms": {"p50": 1.2}}})
        if path == ac.WARM_PATH:
            return 200, "[]"
        b = next(bodies)
        return (200, b) if b is not None else (503, "upstream")

    monkeypatch.setattr(ac, "_http", fake_http)
    assert ac.serve_and_probe(st, "http://x", uid) == 1        # 1 == some read errored
    out = capsys.readouterr().out
    assert "offers=1 null=1 errors=1 of 3 reads" in out
    assert "CNN" in out and "WSJ" in out                        # the payload is shown, not just counted
    assert "rec_story_index_hit_total" in out                   # metrics reported from the server


def test_the_warm_path_is_a_route_the_app_actually_serves():
    """Pinned against the app's REAL route table. The first production run of --serve spent six
    retries against `/api/me/recommendations`, which does not exist, then reported "no offers" —
    a conclusion about a 404 rather than about the feature. A guessed path is not a probe."""
    pytest.importorskip("fastapi")
    import importlib.util
    spec = importlib.util.spec_from_file_location("_api_probe", ROOT / "examples" / "api_fastapi.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_api_probe"] = mod
    spec.loader.exec_module(mod)
    paths = {getattr(r, "path", None) for r in mod.app.routes}
    assert ac.WARM_PATH in paths, f"{ac.WARM_PATH} is not a route; app has {sorted(p for p in paths if p and 'recommend' in p)}"


def test_a_404_warm_aborts_rather_than_retrying(st, monkeypatch, capsys):
    """404 joins 0 and 401 as terminal: a wrong path never becomes a right one by waiting."""
    import time
    calls, slept = [], []
    monkeypatch.setattr(ac, "_http",
                        lambda base, path, hdr, timeout=90:
                        (calls.append(path), (200, "{}") if path == "/api/metrics" else (404, "nf"))[1])
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    uid = st.upsert_user_by_identity("google", "acct-5", email="z@example.com").id
    assert ac.serve_and_probe(st, "http://x", uid) == 2
    assert sum(1 for p in calls if p == ac.WARM_PATH) == 1
    assert slept == []
    assert "aborting the warm loop" in capsys.readouterr().out
