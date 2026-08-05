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
    counter, drift, _, _testable = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
    assert not drift, drift
    assert counter[expected] == 1
    assert bool(counter.get("ELIGIBLE")) is eligible


def test_a_syndicated_reprint_is_not_a_second_outlet(st, uid):
    """The audit collapses outlet identity the same way the resolver does; without that it would
    over-report the eligible rate on exactly the clusters syndication dominates."""
    story = _story([_member(ANCHOR, "Sportskeeda", -0.6), _member(NEAR, "Sportskeeda.Com", 0.6)])
    assert ac._attribute(st, uid, ANCHOR, _index(story)) == "no_unread_other_outlet"
    _, drift, _, _testable = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
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
    _, drift, _, _testable = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
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


def test_stale_reads_are_reported_as_testable_now(st, uid):
    """The operator's real question is "which article do I open?", and after a few hours every
    stored read is stale, so the offers list is empty and answers nothing. The stale_read bucket is
    exactly the set that WOULD produce a strip when read again."""
    from datetime import datetime, timedelta, timezone
    story = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    _read(st, uid, ANCHOR, "CNN")

    # fresh: it is an offer, and there is nothing to suggest re-reading
    _c, _d, examples, testable = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
    assert examples and not testable

    # stale: no offer, but it IS the article to open
    import story_continuation as sc_mod
    real = sc_mod.freshness_hours
    try:
        sc_mod.freshness_hours = lambda: 0.0000001
        counter, _d, examples, testable = ac._run(st, _index(story), [(uid, ANCHOR)], 50, 5)
    finally:
        sc_mod.freshness_hours = real
    assert counter["stale_read"] == 1 and not examples
    assert [u for _h, u in testable] == [ANCHOR]


def test_suggest_lists_unread_members_that_would_fire(st, uid, capsys):
    """The question every by-hand test has, and the one nothing else answered. Freshness passes for
    free on an UNREAD member — there is no stored read — so ELIGIBLE here means exactly "open this
    and the strip appears"."""
    story = _story([_member(ANCHOR, "CNN", -0.6), _member(NEAR, "The Wall Street Journal", 0.6)])
    idx = _index(story)

    assert ac.suggest(st, idx, uid, 5) == 0
    out = capsys.readouterr().out
    assert ANCHOR in out and NEAR in out, "both members are unread and both would fire"

    # …and an article the reader has already read is never suggested: re-reading it cannot help,
    # because add_read is idempotent and keeps the original timestamp.
    _read(st, uid, ANCHOR, "CNN")
    ac.suggest(st, idx, uid, 5)
    out = capsys.readouterr().out
    assert ANCHOR not in out


# --------------------------------------------------------------------------- --counters
def _api_module():
    """The REAL api module, so counter NAMES are pinned against their emitter rather than against a
    second copy of the same string. The last time this script and the app each held their own idea
    of a shared constant, `--serve` probed a route that does not exist and reported "no offers"."""
    pytest.importorskip("fastapi")
    if "_api_probe" not in sys.modules:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_api_probe",
                                                      ROOT / "examples" / "api_fastapi.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_api_probe"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_api_probe"]


def _line(out: str, starts: str) -> str:
    return next(ln for ln in out.splitlines() if ln.strip().startswith(starts))


def test_counters_mode_reports_the_labels_the_endpoint_actually_WRITES(monkeypatch, capsys):
    """The whole value of this mode is telling an offer apart from a null. If the audit read a label
    the endpoint does not write, it would report a confident `offer 0` forever — which is exactly
    the false negative the mode exists to prevent."""
    import obs_metrics
    api = _api_module()
    api._continuation_outcome("offer")
    api._continuation_outcome("null")
    api._continuation_outcome("null")
    counters = obs_metrics.snapshot()["counters"]
    offers = counters["continuation_result_total|offer"]      # KeyError here IS the failure
    nulls = counters["continuation_result_total|null"]
    assert offers >= 1 and nulls >= 2

    monkeypatch.setattr(ac, "_http",
                        lambda base, path, hdr, timeout=20: (200, json.dumps({"counters": counters})))
    assert ac.report_counters("http://x") == 0
    out = capsys.readouterr().out
    assert f"offer     {offers:>6,}" in out
    assert f"null      {nulls:>6,}" in out
    # Every label, not offer+null: obs_metrics counters are process-global, so whatever else has
    # already exercised the endpoint in this session (`disabled`, `error`) is in the same snapshot
    # and belongs in the total. Asserting the narrower sum passed alone and failed in the suite.
    total = sum(v for k, v in counters.items() if k.startswith("continuation_result_total|"))
    assert f"answered {total:,} time(s)" in out


def test_a_cold_index_is_named_so_nulls_are_not_read_as_no_offer(monkeypatch, capsys):
    """A cold index makes every answer a null that means "no story view", not "this reader has no
    continuation". Reporting the two identically sends the next hour at the wrong layer."""
    body = json.dumps({"counters": {"continuation_result_total|null": 12}})
    monkeypatch.setattr(ac, "_http", lambda base, path, hdr, timeout=20: (200, body))
    assert ac.report_counters("http://x") == 0
    assert "COLD" in capsys.readouterr().out


def test_a_cold_index_with_no_answers_explains_nothing_away(monkeypatch, capsys):
    """Immediately after a restart there are no answers, so "every answer above is a null" is a
    confident claim about an empty set. What the operator needs there is the ORDER — warm, then
    read — because a read taken before the index exists produces a null that means nothing."""
    body = json.dumps({"counters": {}})
    monkeypatch.setattr(ac, "_http", lambda base, path, hdr, timeout=20: (200, body))
    assert ac.report_counters("http://x") == 0
    out = capsys.readouterr().out
    assert "COLD" in out
    assert "every answer above" not in out
    assert "only THEN read" in out


def test_a_warm_index_is_not_called_cold(monkeypatch, capsys):
    body = json.dumps({"counters": {"continuation_result_total|null": 12,
                                    "rec_story_index_hit_total": 3}})
    monkeypatch.setattr(ac, "_http", lambda base, path, hdr, timeout=20: (200, body))
    assert ac.report_counters("http://x") == 0
    assert "COLD" not in capsys.readouterr().out


def test_counters_mode_reports_an_unreachable_metrics_endpoint(monkeypatch, capsys):
    """Silence and zero must not look alike: `offer 0` from a server that never answered is not
    evidence about the feature."""
    monkeypatch.setattr(ac, "_http",
                        lambda base, path, hdr, timeout=20: (0, "URLError: connection refused"))
    assert ac.report_counters("http://x") == 2
    assert "connection refused" in capsys.readouterr().out


def test_counters_mode_needs_no_store(monkeypatch, capsys):
    """It runs before the store is opened, so it still answers when the DB is the thing that is
    wrong — and it never pays for a story index."""
    monkeypatch.setattr(store_mod, "Store",
                        lambda *a, **k: pytest.fail("--counters must not open the store"))
    monkeypatch.setattr(er, "story_index",
                        lambda *a, **k: pytest.fail("--counters must not build an index"))
    monkeypatch.setattr(ac, "_http",
                        lambda base, path, hdr, timeout=20: (200, json.dumps({"counters": {}})))
    monkeypatch.setattr(sys, "argv", ["audit_continuation.py", "--counters"])
    assert ac.main() == 0
    assert "nothing yet" in capsys.readouterr().out


# --------------------------------------------------------------------------- --events
def _event(st, uid, name, props=None):
    # `record_analytics_events` takes snake_case (it writes the ORM row); `list_analytics_events`
    # returns camelCase. Getting that backwards writes a row with a NULL user_id that every
    # per-reader filter silently drops — which is what the first draft of these tests did.
    st.record_analytics_events([{
        "event": name, "user_id": uid, "anon_id": "a1", "session_id": "s1",
        "props": props or {"storyId": "s-1"},
        "client_ts": "2026-08-05T10:00:00+00:00", "server_ts": "2026-08-05T10:00:00+00:00",
    }])


def test_events_mode_names_the_stage_that_failed(st, uid, capsys):
    """The reason this mode exists. --counters ends at the engine; from there on the only witnesses
    are these events, and an armed-but-never-shown offer has a different cause from an
    eligible-but-never-armed one."""
    _event(st, uid, "continuation_eligible")
    _event(st, uid, "continuation_armed")
    assert ac.report_events(st, uid) == 0
    out = capsys.readouterr().out
    assert "LOST BEFORE RENDER" in out
    assert "dwell gate" in out and "DIFFERENT page" in out and "impressions" in out


def test_events_mode_separates_a_storage_failure_from_a_render_failure(st, uid, capsys):
    _event(st, uid, "continuation_eligible")
    assert ac.report_events(st, uid) == 0
    out = capsys.readouterr().out
    assert "LOST AT ARMING" in out
    assert "LOST BEFORE RENDER" not in out


def test_events_mode_reports_shown_by_surface(st, uid, capsys):
    """`surface` is the measurement design §9.1.1 says would overturn the primary-surface choice, so
    the probe has to actually break it out rather than report one blended number."""
    for name in ("continuation_eligible", "continuation_armed"):
        _event(st, uid, name)
    _event(st, uid, "continuation_shown", {"storyId": "s-1", "surface": "card"})
    _event(st, uid, "continuation_shown", {"storyId": "s-2", "surface": "story"})
    assert ac.report_events(st, uid) == 0
    out = capsys.readouterr().out
    assert "shown by surface" in out and "card" in out and "story" in out
    assert "LOST" not in out                     # nothing failed — say nothing


def test_events_mode_does_not_confuse_an_old_deploy_with_a_silent_browser(st, uid, capsys):
    """Empty is genuinely ambiguous: before the allow-list fix the sink dropped all six, so an empty
    result from an older build says nothing at all about the client. Claiming otherwise would send
    the next investigation at the wrong layer."""
    assert ac.report_events(st, uid) == 0
    out = capsys.readouterr().out
    assert "none recorded" in out
    assert "allow-list dropped all six" in out           # the old-deploy caveat survives
    # …but it must LEAD with the ordinary cause. Production read 0 offers and 0 events, and the
    # first draft's wording ("the events never arrived — check /api/events") pointed at a transport
    # failure when the honest reading was that the engine had declined every read.
    assert "EXPECTED result unless --counters shows offer > 0" in out
    lead, rest = out.split("allow-list dropped all six", 1)
    assert "EXPECTED result" in lead, "the ordinary cause must come first, not as a footnote"


def test_events_mode_ignores_other_readers_and_other_events(st, uid, capsys):
    other = st.upsert_user_by_identity("dev", "someone-else").id
    _event(st, uid, "continuation_eligible")
    _event(st, other, "continuation_eligible")
    _event(st, uid, "article_read", {"source": "discover"})
    assert ac.report_events(st, uid) == 0
    out = capsys.readouterr().out
    assert "1 continuation event(s) for reader" in out


def test_events_mode_names_why_a_return_was_suppressed(st, uid, capsys):
    """`capped` and `dismissed` live in localStorage and OUTLIVE the session — and they accumulated
    while these events were being dropped, so a story can be at the cap with no record of ever
    having been shown. Reporting the count without the reason would leave that undiagnosable."""
    _event(st, uid, "continuation_eligible")
    _event(st, uid, "continuation_armed")
    _event(st, uid, "continuation_suppressed", {"storyId": "s-1", "reason": "capped"})
    assert ac.report_events(st, uid) == 0
    out = capsys.readouterr().out
    assert "suppressed because" in out and "capped" in out


def test_events_mode_reports_arming_that_happened_in_a_backgrounded_tab(st, uid, capsys):
    """The precondition for the trigger failing silently: the card enables its visibility listener
    while the tab is already hidden. Counting it separates "the gates rejected the return" from
    "the return was never observed", which no other signal here can do."""
    _event(st, uid, "continuation_eligible")
    _event(st, uid, "continuation_armed", {"storyId": "s-1", "hidden": True})
    assert ac.report_events(st, uid) == 0
    out = capsys.readouterr().out
    assert "armed while the tab was ALREADY hidden: 1 of 1" in out
    assert "deferred" in out or "defers" in out       # the explanation, only when nothing was shown


def test_a_hidden_arm_is_not_blamed_when_the_strip_did_render(st, uid, capsys):
    """Arming while hidden is NORMAL — it is the common ordering, and ca7f6f1 exists to make it
    work. Flagging it whenever it happens would manufacture a suspect out of the design."""
    _event(st, uid, "continuation_eligible")
    _event(st, uid, "continuation_armed", {"storyId": "s-1", "hidden": True})
    _event(st, uid, "continuation_shown", {"storyId": "s-1", "surface": "card"})
    assert ac.report_events(st, uid) == 0
    out = capsys.readouterr().out
    assert "armed while the tab was ALREADY hidden: 1 of 1" in out
    assert "defers" not in out
