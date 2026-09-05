"""The four findings from the first production run of platform-enable.sh (2026-09-05), each pinned.

1. /v1/health counted enrichment on the request path (3.5 s at 150k rows; a 10 s probe timed
   out on a cold engine) — now a background count with a TTL, and index-driven.
2. The identity backfill wrote one transaction per row and died on the first lock error at ~37k
   of 150k rows, with the traceback sent to /dev/null — now a transaction per batch, retried,
   non-fatal, passes until complete, exit code and one-line summary.
3. The first recorded story build (2,897 stories, ~100k joins) was one transaction that failed
   whole, leaving builds with no stories, forever — now chunked, stats written last.
4. A build row with no story rows made the platform serve an EMPTY page marked stale — now it
   builds instead.
"""

import json
import pathlib
import sys
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import identity  # noqa: E402
import identity_backfill  # noqa: E402
import rss_ingest  # noqa: E402
import store as store_mod  # noqa: E402
import story_history  # noqa: E402
import story_service  # noqa: E402
from platform_api import app as platform_app  # noqa: E402
from platform_api import metering, routes  # noqa: E402

E = rss_ingest.FeedEntry


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RWE_PLATFORM_API", "1")
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "36500")
    metering.reset()
    story_service.clear_cache()
    yield
    story_service.clear_cache()


def _seed(url="sqlite:///:memory:", n_extra=0):
    st = store_mod.Store(url)
    scorer = rss_ingest.make_scorer()
    entries = [
        E(url="https://www.bbc.co.uk/news/articles/abc123", title="Prime minister resigns after vote",
          published_at="2026-09-01T10:00:00+00:00", publisher_hint="bbc.co.uk"),
        E(url="https://www.theguardian.com/politics/2026/sep/01/pm-resigns",
          title="Prime minister resigns after confidence vote", published_at="2026-09-01T11:00:00+00:00",
          publisher_hint="theguardian.com"),
        E(url="https://www.npr.org/2026/09/01/pm-resigns-vote", title="Prime minister resigns after losing vote",
          published_at="2026-09-01T13:00:00+00:00", publisher_hint="npr.org"),
    ]
    for i in range(n_extra):
        entries.append(E(url=f"https://www.bbc.co.uk/news/extra-{i}", title=f"Filler story number {i} about nothing",
                         published_at="2026-09-01T09:00:00+00:00", publisher_hint="bbc.co.uk"))
    rss_ingest.ingest_entries(entries, "BBC", "https://feeds.bbci.co.uk/news/rss.xml", scorer, st, source_type="rss")
    identity.sync_publishers(st)
    return st


def _strip_identity(st):
    """Make the rows look like a legacy catalogue (no ids), as the backfill finds them."""
    with st.session() as s:
        s.execute(store_mod.update(store_mod.FeedArticle).values(article_id=None, publisher_id=None, licence_class=None))
        s.execute(store_mod.delete(store_mod.ArticleAlias))
        s.execute(store_mod.delete(store_mod.ArticleProvenance))
        s.commit()


# ---- 1. health is cheap ----------------------------------------------------------------------- #

def test_health_never_counts_enrichment_on_the_request_path(monkeypatch, tmp_path):
    st = _seed(f"sqlite:///{tmp_path}/h.db")                     # file-backed: a connection per thread, as in production
    assert not st.single_connection
    calls, finished = [], []
    real = st.enrichment_coverage

    def slow(**kw):
        calls.append(time.time())
        time.sleep(1.5)                                           # far longer than any request path
        out = real(**kw)
        finished.append(time.time())
        return out
    monkeypatch.setattr(st, "enrichment_coverage", slow)
    c = TestClient(platform_app.create_app(st))
    first = c.get("/v1/health").json()["data"]
    # The invariant, not a wall-clock bound (a 0.25 s bound flaked at 0.96 s under the full
    # suite's load): the response is back while the count is still running.
    assert calls and not finished, "the count must not be on the request path"
    assert first["status"] == "ok" and first["enrichment"] is None
    deadline = time.time() + 8
    while time.time() < deadline:
        d = c.get("/v1/health").json()["data"]
        if d["enrichment"]:
            break
        time.sleep(0.1)
    assert d["enrichment"]["catalogue"]["articles"] == 3
    assert len(calls) == 1                                        # one count, cached for ENRICHMENT_TTL
    assert routes.ENRICHMENT_TTL >= 60


def test_health_counts_inline_when_the_store_shares_one_connection(monkeypatch):
    """In-memory SQLite hands every session the same connection: a counting thread beside a
    request interleaves two transactions on it (the full suite saw a key row's UPDATE match
    nothing). Such a store counts inline; no thread is started."""
    import threading
    st = _seed()                                                  # sqlite:///:memory: -> StaticPool
    assert st.single_connection
    started = []
    real_start = threading.Thread.start

    def spy(self, *a, **kw):
        started.append(self.name)
        return real_start(self, *a, **kw)
    monkeypatch.setattr(threading.Thread, "start", spy)
    c = TestClient(platform_app.create_app(st))
    first = c.get("/v1/health").json()["data"]
    assert first["enrichment"]["catalogue"]["articles"] == 3     # the first answer carries the count
    assert "platform-enrichment" not in started


def test_enrichment_counts_are_driven_from_the_side_tables():
    import location
    st = _seed()
    bbc = "https://bbc.co.uk/news/articles/abc123"
    st.replace_article_entities(bbc, {"person": ["keir starmer"], "org": ["labour"]})
    st.replace_article_entities(bbc, {"span": ["downing street"]}, source="headline-caps")
    st.replace_article_event_locations(bbc, [location.EventLocation(country="GB", source="gdelt-gkg"),
                                             location.EventLocation(country="FR", source="gdelt-gkg")])
    e = st.enrichment_coverage()["catalogue"]
    # distinct ARTICLES, never rows: two entities and two locations on one article count once
    assert e == {"articles": 3, "withEntities": 1, "entityCoverage": 0.333, "withSpans": 1,
                 "spanCoverage": 0.333, "withEventCountries": 1, "geoCoverage": 0.333}


# ---- 2. the backfill finishes -------------------------------------------------------------------- #

def test_backfill_writes_a_batch_per_transaction_and_survives_lock_errors(monkeypatch):
    st = _seed(n_extra=7)                                        # 10 rows, batches of 3
    _strip_identity(st)
    real = st.apply_identity_backfill_batch
    failures = {"left": 2}

    def flaky(rows):
        if failures["left"]:
            failures["left"] -= 1
            raise OperationalError("UPDATE feed_articles", {}, Exception("database is locked"))
        return real(rows)
    monkeypatch.setattr(st, "apply_identity_backfill_batch", flaky)
    monkeypatch.setattr(identity_backfill.time, "sleep", lambda s: None)
    lines = []
    stats = identity_backfill.run(st, batch=3, log=lines.append)
    assert stats["batches"] == 4 and stats["failedBatches"] == 0 and stats["changed"] == 10
    assert identity_backfill.run(st, batch=3, dry_run=True, log=lines.append)["missingArticleId"] == 0


def test_backfill_reports_a_batch_that_never_lands_and_a_second_pass_picks_it_up(monkeypatch):
    st = _seed(n_extra=7)
    _strip_identity(st)
    real = st.apply_identity_backfill_batch
    state = {"poison": True}

    def poisoned(rows):
        if state["poison"] and len(rows) == 3 and rows[0]["canonical_url"] == sorted(r["canonical_url"] for r in rows)[0]:
            state["poison"] = False                              # fail the first batch every attempt, once
            raise OperationalError("x", {}, Exception("database is locked"))
        return real(rows)

    def always_fail_first(rows):
        raise OperationalError("x", {}, Exception("database is locked"))
    monkeypatch.setattr(identity_backfill.time, "sleep", lambda s: None)
    monkeypatch.setattr(st, "apply_identity_backfill_batch", always_fail_first)
    lines = []
    one = identity_backfill.run(st, batch=3, log=lines.append)
    assert one["failedBatches"] == 4 and one["changed"] == 0
    assert any('"identity_backfill_batch_failed"' in ln for ln in lines)
    monkeypatch.setattr(st, "apply_identity_backfill_batch", real)
    done = identity_backfill.run_until_complete(st, batch=3, log=lines.append)
    assert done["passes"] == 1 and done["changed"] == 10 and done["missingArticleId"] == 0
    assert identity_backfill.run(st, batch=3, dry_run=True, log=lines.append)["missingArticleId"] == 0


def test_backfill_cli_prints_one_summary_line_and_exits_nonzero_while_rows_remain(tmp_path, capsys, monkeypatch):
    db = f"sqlite:///{tmp_path}/bf.db"
    st = _seed(db)
    _strip_identity(st)
    assert identity_backfill.main(["--db", db, "--dry-run"]) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(last)["missingArticleId"] == 3
    assert identity_backfill.main(["--db", db]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    summary = json.loads(out[-1])
    assert summary["missingArticleId"] == 0 and summary["changed"] == 3 and summary["passes"] == 1
    assert len(out) <= 3                                          # progress every LOG_EVERY batches, not every batch
    # rows that cannot be filled -> exit 1, still one summary line
    monkeypatch.setattr(store_mod.Store, "apply_identity_backfill_batch",
                        lambda self, rows: (_ for _ in ()).throw(OperationalError("x", {}, Exception("locked"))))
    monkeypatch.setattr(identity_backfill.time, "sleep", lambda s: None)
    _strip_identity(store_mod.Store(db))
    assert identity_backfill.main(["--db", db, "--passes", "2"]) == 1
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["missingArticleId"] == 3


# ---- 3. story history at scale ------------------------------------------------------------------- #

def test_story_history_writes_in_chunks_and_stats_last(monkeypatch):
    st = _seed()
    story_service.clear_cache()
    stories = story_service.default_story_view(st, build_inline=True)
    assert len(stories) == 1
    # a first build with many joins: every chunk is its own transaction
    opened = []
    real_session = st.session

    def counting_session():
        opened.append(1)
        return real_session()
    monkeypatch.setattr(st, "session", counting_session)
    out = story_history.record_build(st, stories, build_version="1", config_hash="x", resolve_ids=st.article_ids_for_urls)
    assert out["joins"] == 3 and out["new_stories"] == 1 and out["changed"] == 1
    monkeypatch.setattr(st, "session", real_session)
    h = st.story_history(stories[0]["id"])
    assert len(h["snapshots"]) == 1 and len(h["membership"]) == 3 and h["story"]["snapshots"] == 1
    assert h["story"]["title"] == stories[0]["title"]
    b = st.story_builds(limit=1)[0]
    assert b["stories"] == 1 and b["joins"] == 3 and b["ms"] is not None       # the completion marker
    # a chunk size of 1 forces every row through its own transaction and the result is identical
    st2 = _seed()
    stories2 = story_service.default_story_view(st2, build_inline=True)
    real_apply = st2.apply_story_history
    monkeypatch.setattr(st2, "apply_story_history", lambda **kw: real_apply(**dict(kw, chunk=1)))
    story_history.record_build(st2, stories2, build_version="1", config_hash="x", resolve_ids=st2.article_ids_for_urls)
    h2 = st2.story_history(stories2[0]["id"])
    assert len(h2["membership"]) == 3 and len(h2["snapshots"]) == 1
    # a second unchanged build: touched stamps move, nothing else is written
    story_history.record_build(st2, stories2, build_version="1", config_hash="x", resolve_ids=st2.article_ids_for_urls)
    h3 = st2.story_history(stories2[0]["id"])
    assert len(h3["snapshots"]) == 1 and len(h3["membership"]) == 3 and h3["story"]["lastBuildId"] == 2


def test_a_failed_history_write_leaves_a_build_without_stats_and_the_next_build_completes_it(monkeypatch):
    st = _seed()
    stories = story_service.default_story_view(st, build_inline=True)
    real = st.apply_story_history
    monkeypatch.setattr(st, "apply_story_history", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        story_history.record_build(st, stories, build_version="1", config_hash="x")
    b = st.story_builds(limit=1)[0]
    assert b["stories"] == 1 and b["ms"] is None and b["joins"] == 0     # recorded, never completed
    assert st.story_history(stories[0]["id"]) is None
    monkeypatch.setattr(st, "apply_story_history", real)
    story_history.record_build(st, stories, build_version="1", config_hash="x")
    assert len(st.story_history(stories[0]["id"])["membership"]) == 3
    assert st.story_builds(limit=1)[0]["joins"] == 3


# ---- 4. an empty record is never served as stale ------------------------------------------------- #

def test_platform_builds_rather_than_serving_an_empty_recorded_build(monkeypatch):
    st = _seed()
    st.platform_create_tenant("t", "T", kind="internal")
    secret, _ = st.platform_mint_key(tenant_id="t", plan="internal")
    h = {"Authorization": f"Bearer {secret}"}
    # a build row exists (as production had) but no story rows landed
    st.record_story_build(built_at="2026-09-05T13:00:00+00:00", build_version="1", config_hash="x",
                          registry_version=None, catalog_rows=3, catalog_newest=None, stories=1)
    spawned = []
    monkeypatch.setattr(story_service, "_spawn_refresh", lambda store_, logical: spawned.append(logical))
    c = TestClient(platform_app.create_app(st))
    r = c.get("/v1/stories", headers=h).json()
    assert r["meta"]["page"]["total"] == 1 and r["meta"]["stale"] is False and spawned == []
    assert c.get(f"/v1/stories/{r['data'][0]['storyId']}", headers=h).status_code == 200


# ---- 5. served ids are unique, so the history record lands (second production run) ------------- #

def _cov(*urls, **extra):
    return dict({"coverage": [{"url": u, "publisher": f"P{u}", "publishedAt": "2026-09-01T00:00:00+00:00"}
                              for u in urls]}, **extra)


def test_a_split_never_serves_two_stories_under_one_id():
    """The production shape: the ledger hands `st_one` to the piece holding most of the old
    coverage, while the piece that kept the ANCHOR article derives `st_one` again. The claim
    keeps the id; the other piece is re-anchored, deterministically, and never collides."""
    class Ledger:
        def story_member_ids(self):
            return {u: "st_one" for u in "abcd"}
    stories = [_cov("a", "b", "c", id="st_bigpiece_derived"), _cov("d", id="st_one")]
    out = story_service.stabilize_ids_readonly(Ledger(), stories)
    assert out[0]["id"] == "st_one", "the piece holding most of the coverage claims the ledger id"
    assert out[1]["id"] != "st_one" and out[1]["id"].startswith("st_") and len(out[1]["id"]) == 19
    assert len({s["id"] for s in out}) == 2
    again = story_service.stabilize_ids_readonly(Ledger(), [_cov("a", "b", "c", id="st_bigpiece_derived"),
                                                            _cov("d", id="st_one")])
    assert [s["id"] for s in again] == [s["id"] for s in out], "deterministic across builds"


def test_unique_ids_keeps_claims_and_rehashes_the_rest():
    stories = [{"id": "st_x", "coverage": []}, {"id": "st_x", "coverage": []}, {"id": "st_y", "coverage": []},
               {"id": "st_x", "coverage": []}]
    out = story_service.unique_ids(stories, keep={1})
    assert out[1]["id"] == "st_x", "the claiming story keeps its id wherever it sits in the list"
    assert out[0]["id"] != "st_x" and out[3]["id"] not in {"st_x", out[0]["id"]} and out[2]["id"] == "st_y"
    assert len({s["id"] for s in out}) == 4
    assert story_service.unique_ids(stories, keep={1}) == out
    same = [{"id": "st_a", "coverage": []}, {"id": "st_b", "coverage": []}]
    assert story_service.unique_ids(same) is same, "nothing to do: the list is handed back as it is"


def test_record_build_survives_a_duplicate_id_and_records_the_first(caplog):
    st = _seed()
    dup = [_cov("https://x.example/1", id="st_dup", title="A"), _cov("https://x.example/2", id="st_dup", title="B")]
    with caplog.at_level("WARNING", logger="story_history"):
        out = story_history.record_build(st, dup, build_version="1", config_hash="x")
    assert out["stories"] == 1 and out["new_stories"] == 1
    assert st.story_history("st_dup")["story"]["title"] == "A"
    assert any("story_history_duplicate_ids" in r.getMessage() for r in caplog.records)


def test_health_carries_the_recorders_last_error_and_its_counts(monkeypatch):
    st = _seed()
    monkeypatch.setattr(story_service, "_HISTORY_STATUS",
                        {"lastOk": None, "lastError": None, "lastErrorAt": None, "errors": 0})
    st.platform_create_tenant("t", "T", kind="internal")
    secret, _ = st.platform_mint_key(tenant_id="t", plan="internal")
    h = {"Authorization": f"Bearer {secret}"}
    c = TestClient(platform_app.create_app(st))
    real = st.apply_story_history

    def boom(**kw):
        raise RuntimeError("UNIQUE constraint failed: stories.story_id")
    monkeypatch.setattr(st, "apply_story_history", boom)
    assert c.get("/v1/stories", headers=h).status_code == 200      # served exactly as before
    hist = c.get("/v1/health").json()["data"]["history"]
    assert hist["errors"] == 1 and hist["lastError"].startswith("RuntimeError: UNIQUE constraint")
    assert hist["stories"] == 0 and hist["lastErrorAt"]
    monkeypatch.setattr(st, "apply_story_history", real)
    story_service.clear_cache()
    assert c.get("/v1/stories", headers=h).status_code == 200
    hist = c.get("/v1/health").json()["data"]["history"]
    assert hist["stories"] == 1 and hist["story_snapshots"] == 1 and hist["lastOk"]["stories"] == 1
    assert hist["errors"] == 1 and hist["lastError"], "the error since start stays visible"
