"""The Recommendation Evaluation Engine (rec_sandbox) — S1 contract tests.

Pins the four properties the architecture review promised, each against the REAL engine stack
(no mocks): ZERO WRITES (the store's bytes, the repo's data/ directory, and the tempdir are
untouched by a full evaluation), DETERMINISM (same store + spec -> byte-identical report),
LAYER PARITY (a zero-injection evaluation serves exactly what the engine's own entry points
serve — the sandbox adds no recommendation logic), and HONEST GATES (freshness drops, the
qbias builder's lean-resolvability drop, corpus-validation failures, and below-threshold
readers are reported, never bypassed). Also pins the baseline-reuse rule: a provided baseline
object is consulted for its ``.backend`` ONLY.
"""
import glob
import hashlib
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import corpus_health                                        # noqa: E402
import corpus_refresh                                       # noqa: E402
import evidence_resolver as er                              # noqa: E402
import rec_sandbox                                          # noqa: E402
import store as store_mod                                   # noqa: E402

_ENV = {"RWE_N_USERS": "120", "RWE_MAX_ITEMS": "300"}

PUBS = ["AP", "Reuters", "NPR", "BBC News", "The Guardian", "The Hill", "Fox News", "CNN"]
STORY = [("AP", "senate budget vote reaches bipartisan deal"),
         ("CNN", "senate passes budget vote after bipartisan deal"),
         ("Fox News", "bipartisan budget deal clears senate vote")]


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture(scope="module", autouse=True)
def _sized_population():
    """Pin the synthetic-population sizing for every build in this module (and restore)."""
    old = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    yield
    for k, v in old.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    """A frozen catalog: 61 token-disjoint articles + one 3-publisher story cluster, one
    measured reader (6 reads) and one empty reader."""
    p = tmp_path_factory.mktemp("sandbox") / "sandbox.db"
    st = store_mod.Store(f"sqlite:///{p}")
    for k in range(61):
        pub = PUBS[k % 8]
        url = f"https://{pub.split()[0].lower()}{k % 8}.example.com/sbx/{k}"
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=f"sbx{k}a sbx{k}b sbx{k}c sbx{k}d", description="d", body=None,
            published_at=_iso(0.5 + (k % 6) * 0.4), source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": (-1.0, 0.0, 1.0)[k % 3], "political": True, "title": f"sbx{k}"})
    for i, (pub, title) in enumerate(STORY):
        url = f"https://{pub.split()[0].lower()}.example.com/story/{i}"
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title, description="d", body=None, published_at=_iso(1.0 + 0.1 * i),
            source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": (-1.0, 0.0, 1.0)[i % 3], "political": True, "title": title})
    return p


@pytest.fixture(scope="module")
def store(db_path):
    return store_mod.Store(f"sqlite:///{db_path}")


@pytest.fixture(scope="module")
def reader(store):
    uid = store.upsert_user_by_identity("dev", "sandbox-reader", display_name="Reader").id
    for k in range(6):
        url = f"https://ap0.example.com/sbx/{k * 8}"
        store.add_read(uid, er._canon(url),
                       {"article_id": er._canon(url), "outlet": "AP", "category": "Politics",
                        "lean": -1.0, "political": True, "title": f"sbx{k * 8}"},
                       _iso(0.2 + k * 0.1), read_source="test")
    return uid


@pytest.fixture(scope="module")
def empty_reader(store):
    return store.upsert_user_by_identity("dev", "sandbox-empty", display_name="Empty").id


INJECT_STORY = {"url": "https://apnews.com/article/senate-budget-analysis",
                "title": "senate budget vote bipartisan deal analysis"}
INJECT_UNKNOWN = {"url": "https://unknown-blog.example/post", "title": "mystery post"}
INJECT_STALE = {"url": "https://reuters.com/very/old", "title": "ancient news"}
INJECT_LONER = {"url": "https://npr.org/exclusive/zebra",
                "title": "zebra quartet wins improbable chess marathon"}


@pytest.fixture(scope="module")
def full(store, reader, db_path):
    """ONE full evaluation (compare mode, every question, four injection archetypes) shared by
    the assertion tests — with the isolation evidence captured around it."""
    spec = {
        "inject": [dict(INJECT_STORY, publishedAt=_iso(0.3)),
                   dict(INJECT_UNKNOWN, publishedAt=_iso(0.2)),
                   dict(INJECT_STALE, publishedAt=_iso(400)),
                   dict(INJECT_LONER, publishedAt=_iso(0.4))],
        "ask": ["https://cnn7.example.com/sbx/7"],
        "readers": [{"kind": "demo"}, {"kind": "user", "id": reader}],
        "strategies": [None],
        "params": [None],
        "compare": True,
    }
    db_before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    data_dir = ROOT / "data"
    data_before = sorted((f.name, f.stat().st_mtime_ns, f.stat().st_size)
                         for f in data_dir.glob("*")) if data_dir.exists() else []
    tmp_before = set(glob.glob(os.path.join(tempfile.gettempdir(), "ih_refresh_*.csv")))

    report = rec_sandbox.evaluate(store, spec)

    return SimpleNamespace(
        report=report, spec=spec,
        db_before=db_before,
        db_after=hashlib.sha256(db_path.read_bytes()).hexdigest(),
        data_before=data_before,
        data_after=sorted((f.name, f.stat().st_mtime_ns, f.stat().st_size)
                          for f in data_dir.glob("*")) if data_dir.exists() else [],
        tmp_leftover=set(glob.glob(os.path.join(tempfile.gettempdir(),
                                                "ih_refresh_*.csv"))) - tmp_before)


def _injected(full, url):
    return next(e for e in full.report["injected"] if e["url"] == url)


# --------------------------------------------------------------------------- #
# Zero writes, ephemerality, JSON safety.
# --------------------------------------------------------------------------- #
def test_evaluation_writes_nothing_anywhere(full):
    assert full.db_after == full.db_before          # the store's bytes are untouched
    assert full.data_after == full.data_before      # data/ (the serving CSV home) untouched
    assert full.tmp_leftover == set()               # build_active cleaned its tempfile CSV
    json.dumps(full.report, allow_nan=False)        # strictly JSON-safe (no NaN leaks)


def test_report_is_deterministic_across_runs(store, reader):
    spec = {"inject": [dict(INJECT_STORY, publishedAt=_iso(0.3))],
            "readers": [{"kind": "demo"}, {"kind": "user", "id": reader}],
            "params": [None, {"beta": 0.8}]}
    a = rec_sandbox.evaluate(store, spec)
    b = rec_sandbox.evaluate(store, spec)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --------------------------------------------------------------------------- #
# Layer parity: the sandbox serves EXACTLY what the engine's entry points serve.
# --------------------------------------------------------------------------- #
def test_zero_injection_feeds_match_the_engines_own_entry_points(store):
    spec = {"inject": [], "readers": [{"kind": "demo"}],
            "strategies": [None, "rwe-d"], "params": [{"beta": 0.8}]}
    report = rec_sandbox.evaluate(store, spec)

    th = corpus_health.thresholds_from_env()
    candidate = corpus_refresh.build_candidate_for(store, th)
    active, _result, err = rec_sandbox._detached_build(store, candidate, th, generation=-9)
    assert err is None
    be = active.backend
    for feed in report["feeds"]:
        assert feed["status"] == "ok"
        direct = be.recommendations(int(be.demo_user), feed["strategy"], {"beta": 0.8})
        assert [c["id"] for c in feed["served"]] == [r["article"]["id"] for r in direct]


# --------------------------------------------------------------------------- #
# Honest gates + the injection archetypes.
# --------------------------------------------------------------------------- #
def test_fresh_known_outlet_article_becomes_a_ranked_graph_node(full):
    e = _injected(full, INJECT_STORY["url"])
    assert e["disposition"] == "evaluated"
    assert e["resolvedId"] and e["graphNode"] is True
    # the registry canonicalized apnews.com -> its canonical outlet, and the lean resolved
    assert e["scored"]["outlet"] == "Associated Press" and e["scored"]["lean"] is not None
    for x in e["exclusions"]:
        assert x["status"] == "ok"
        assert x["verdict"] in {"recommended", "below_cutoff"}
        if x["verdict"] == "below_cutoff":                    # per-strategy evidence attached
            assert set(x["byStrategy"]) == {"rwe-b", "rwe-d", "adaptive"}
            assert all("rank" in v and "score" in v for v in x["byStrategy"].values())
        assert set(x["paramsUsed"]) == {"rwe-b", "rwe-d", "adaptive"}


def test_story_clustering_detects_the_injected_sibling(full):
    e = _injected(full, INJECT_STORY["url"])
    assert e["story"]["matched"] is True
    assert e["story"]["publisherCount"] >= 3                  # AP + CNN + Fox already covered it
    loner = _injected(full, INJECT_LONER["url"])
    assert loner["story"] == {"matched": False}               # token-disjoint article: no cluster


def test_unknown_outlet_is_dropped_by_the_builder_not_ranked(full):
    e = _injected(full, INJECT_UNKNOWN["url"])
    assert e["disposition"] == "evaluated"                    # it DID enter the composition
    assert e["graphNode"] is False                            # lean unresolved -> no graph node
    assert e["scored"]["lean"] is None
    assert all(x["verdict"] == "not_in_graph" for x in e["exclusions"])


def test_stale_article_is_dropped_by_the_freshness_gate(full):
    e = _injected(full, INJECT_STALE["url"])
    assert e["disposition"] == "dropped_freshness"            # C4: production would never rank it
    assert e["exclusions"] == [] and e["graphNode"] is None
    for feed in full.report["feeds"]:                         # and it never appears in a feed
        assert all(c["url"] != INJECT_STALE["url"] for c in feed["served"])


def test_asked_article_gets_the_same_truthful_verdicts(full):
    assert full.report["asked"], "the ask section must be populated"
    for x in full.report["asked"]:
        assert x["article"] == "https://cnn7.example.com/sbx/7"
        assert x["status"] == "ok" and x["verdict"] in {"recommended", "below_cutoff",
                                                        "seen_excluded"}


def test_below_threshold_reader_is_reported_not_guessed(store, empty_reader):
    spec = {"inject": [dict(INJECT_STORY, publishedAt=_iso(0.3))],
            "readers": [{"kind": "user", "id": empty_reader}]}
    report = rec_sandbox.evaluate(store, spec)
    assert all(f["status"] == "below_threshold" and f["served"] == []
               for f in report["feeds"])
    assert all(x["status"] == "below_threshold"
               for x in report["injected"][0]["exclusions"])


def test_validation_failure_is_the_answer_not_an_exception(store, monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "5000")
    report = rec_sandbox.evaluate(store, {"inject": [dict(INJECT_STORY,
                                                          publishedAt=_iso(0.3))]})
    ev = report["corpus"]["evaluated"]
    assert ev["built"] is False and ev["error"] == "validation_failed"
    assert ev["validation"]["failures"]
    assert all(f["status"] == "not_built" for f in report["feeds"])
    json.dumps(report, allow_nan=False)


# --------------------------------------------------------------------------- #
# Compare mode: diff semantics + the baseline-reuse rule.
# --------------------------------------------------------------------------- #
def test_compare_without_injection_is_identical_by_construction(store, reader):
    report = rec_sandbox.evaluate(store, {"inject": [], "compare": True,
                                          "readers": [{"kind": "user", "id": reader}]})
    assert report["corpus"]["baseline"]["candidateSig"] == \
        report["corpus"]["evaluated"]["candidateSig"]
    assert report["diff"]["perFeed"], "compare mode must produce a per-feed diff"
    assert all(d["identical"] for d in report["diff"]["perFeed"])


class _Poison:
    """Any attribute access proves the sandbox touched a provided personalizer."""
    def __getattr__(self, name):
        raise AssertionError(f"provided baseline personalizer was consulted: .{name}")


def test_provided_baseline_is_consulted_for_its_backend_only(store, reader, full):
    th = corpus_health.thresholds_from_env()
    candidate = corpus_refresh.build_candidate_for(store, th)
    active, _res, err = rec_sandbox._detached_build(store, candidate, th, generation=-8)
    assert err is None
    baseline = SimpleNamespace(backend=active.backend, personalizer=_Poison(),
                               candidate_sig="sig:provided", item_count=active.item_count)
    report = rec_sandbox.evaluate(
        store, {"inject": [dict(INJECT_STORY, publishedAt=_iso(0.3))], "compare": True,
                "readers": [{"kind": "user", "id": reader}]},
        baseline=baseline)
    base_block = report["corpus"]["baseline"]
    assert base_block["provided"] is True
    assert base_block["candidateSig"] == "sig:provided"       # the baseline's own identity
    assert report["diff"]["perFeed"]                          # diff ran through the poison — via
    for d in report["diff"]["perFeed"]:                       # a fresh persist=False personalizer
        assert set(d) >= {"identical", "entered", "left", "moved"}


def test_diff_is_keyed_by_canonical_url_never_q_ids(full):
    for d in (full.report["diff"] or {}).get("perFeed", []):
        for key in d["entered"] + d["left"] + [m["key"] for m in d["moved"]]:
            assert not key.startswith("Q"), f"corpus-relative id leaked into the diff: {key}"


def test_report_contract_v1_is_pinned(full):
    """The frozen surface: version + top-level sections. Additive evolution stays v1; any
    rename/removal must bump reportVersion — this test is the tripwire."""
    assert full.report["reportVersion"] == 1
    assert set(full.report) == {"reportVersion", "spec", "corpus", "injected", "asked",
                                "feeds", "diff", "notes"}
    assert set(full.report["corpus"]) == {"evaluated", "baseline"}
    for e in full.report["injected"]:
        assert {"url", "canonicalUrl", "title", "publisher", "scored", "disposition",
                "resolvedId", "graphNode", "story", "exclusions"} <= set(e)


# --------------------------------------------------------------------------- #
# S2 — the CLI client: a THIN renderer over evaluate(), never a transformer.
# --------------------------------------------------------------------------- #
def test_cli_json_is_byte_identical_to_the_library_report(store, db_path, reader,
                                                          tmp_path, capsys):
    """The no-transformation proof: for the same spec, the CLI's --json output IS the
    library's report."""
    spec = {"inject": [dict(INJECT_STORY, publishedAt=_iso(0.3))],
            "readers": [{"kind": "user", "id": reader}], "params": [None, {"beta": 0.8}]}
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    code = rec_sandbox.main(["--db", f"sqlite:///{db_path}", "--spec", str(spec_file),
                             "--json", "--out", str(tmp_path / "report.json")])
    assert code == 0
    cli_report = json.loads(capsys.readouterr().out)
    lib_report = rec_sandbox.evaluate(store, spec)
    assert json.dumps(cli_report, sort_keys=True) == json.dumps(lib_report, sort_keys=True)
    assert json.loads((tmp_path / "report.json").read_text()) == cli_report


def test_cli_human_render_covers_the_investigation_sections(db_path, reader, capsys):
    code = rec_sandbox.main(["--db", f"sqlite:///{db_path}",
                             "--inject-url", INJECT_STORY["url"],
                             "--inject-title", INJECT_STORY["title"],
                             "--inject-published", _iso(0.3),
                             "--reader", "demo", "--reader", f"user:{reader}",
                             "--ask", "https://cnn7.example.com/sbx/7", "--compare"])
    out = capsys.readouterr().out
    assert code == 0
    # the investigation report reads top-to-bottom through its numbered sections
    for header in ("Recommendation Investigation Report",
                   "1. Evaluation Summary", "2. Reader Context", "3. Reading History",
                   "4. Experiment", "5. Recommendation Feed", "6. Relationship Analysis",
                   "7. Feed Changes", "8. Developer Observations",
                   "9. Recommendation Explanation Matrix", "10. Technical Diagnostics"):
        assert header in out, f"missing section: {header}"
    # plain-English reader labels; --ask verdict is kept (in diagnostics), no data lost
    assert "demo reader" in out and "reader #" in out
    assert "https://cnn7.example.com/sbx/7" in out


def test_cli_exit_code_2_when_the_corpus_does_not_build(db_path, monkeypatch, capsys):
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "5000")
    code = rec_sandbox.main(["--db", f"sqlite:///{db_path}", "--preset", "left"])
    assert code == 2
    out = capsys.readouterr().out
    # plain-English failure, with the raw reason kept visible for diagnosis
    assert "The corpus did not build" in out and "validation_failed" in out


# --------------------------------------------------------------------------- #
# Read-only presentation enrichment: the renderer displays richer info via store
# lookups, without ever altering the report or evaluation (the JSON test above is
# the byte-identity guardrail).
# --------------------------------------------------------------------------- #
def test_measured_reader_shows_history_and_relationship_analysis(db_path, reader, capsys):
    # a store user with reads (the same path the persisted demo / exhibit ACCOUNT uses when
    # investigated as user:<id>) is detected as MEASURED — full history + relationship analysis
    code = rec_sandbox.main(["--db", f"sqlite:///{db_path}", "--reader", f"user:{reader}"])
    out = capsys.readouterr().out
    assert code == 0
    assert "2. Reader Context" in out and "Total reads:" in out
    assert "3. Reading History" in out and "sbx0" in out          # real stored read titles
    assert "6. Relationship Analysis" in out and "Reading Pattern" in out
    assert "Avoids already-read articles" in out                  # history↔feed cross-analysis
    assert "8. Developer Observations" in out
    assert "synthetic reader" not in out.split("2. Reader Context")[1].split("4. Experiment")[0]


def test_reading_history_and_feed_share_a_stacked_layout(db_path, reader, capsys):
    # Presentation contract for the side-by-side view: Reading History and the Recommendation
    # Feed both render each article stacked — Title / Publisher / "Category {bullet} Lean" on
    # their own lines — so a developer can compare the reader's reads against the current
    # recommendations line-for-line. This pins LAYOUT only (structural + glyph-derived, never a
    # pinned rank); the byte-identity test above guards the report/JSON itself.
    code = rec_sandbox.main(["--db", f"sqlite:///{db_path}", "--reader", f"user:{reader}"])
    out = capsys.readouterr().out
    assert code == 0
    g = rec_sandbox._glyphs()                         # same stdout as the render -> same glyphs
    bul = g["bul"]

    # ---- Reading History: header carries the true total, entries are stacked --------------- #
    hist = out.split("3. Reading History")[1].split("5. Recommendation Feed")[0]
    assert "Reading History (6 reads), newest first" in hist        # true count, not "N of M"
    hlines = [ln.strip() for ln in hist.splitlines() if ln.strip()]
    num_idx = [i for i, ln in enumerate(hlines) if ln.rstrip(".").isdigit()]
    assert len(num_idx) == 6                                        # every stored read is listed
    for i in num_idx:                                              # number -> Title / Pub / meta
        title, pub, meta = hlines[i + 1], hlines[i + 2], hlines[i + 3]
        assert title and pub and (bul in meta)                     # 'Category {bullet} Lean'
    assert rec_sandbox._meta_line("Politics", -1.0, g) in hist      # e.g. "Politics • Left"

    # ---- Recommendation Feed: same stacking + a "Why" block with short reasons -------------- #
    feed = out.split("5. Recommendation Feed")[1].split("6. Relationship Analysis")[0]
    flines = [ln.strip() for ln in feed.splitlines() if ln.strip()]
    why_idx = [i for i, ln in enumerate(flines) if ln == "Why"]
    assert why_idx                                                 # the explanation header is "Why"
    for i in why_idx:                                             # each Why sits under Title/Pub/meta
        assert bul in flines[i - 1]                                # 'Category {bullet} Lean' line
        assert flines[i - 2] and flines[i - 3]                     # publisher then title above it
    short_vocab = set(rec_sandbox._WHY_SHORT.values()) | {"Cross-cutting"}
    assert any(v in feed for v in short_vocab)                     # short labels, matching the reads' style
    assert "Why this article?" not in feed                         # the long header/labels are gone


def test_synthetic_row_reader_shows_honest_no_history(db_path, capsys):
    # a TRUE synthetic reader (row:N is always synthetic — unlike demo, which now prefers the
    # persisted demo account when one exists) has no persisted history
    code = rec_sandbox.main(["--db", f"sqlite:///{db_path}", "--reader", "row:3"])
    out = capsys.readouterr().out
    assert code == 0
    assert "No persisted reading history exists for this reader" in out
    assert "--reader user:<id>" in out                            # guides to the persisted account
    assert "not available — synthetic reader" in out
    assert "Total reads:" not in out                             # no fabricated stats


# --------------------------------------------------------------------------- #
# CLI reader resolution: --reader demo prefers the notebook's persisted demo account
# (provider "dev" / demo@infodiet.local) over the synthetic Backend.demo_user, falling back
# when it is absent. CLI-only + read-only; evaluate()/the contract/--json are untouched.
# --------------------------------------------------------------------------- #
def test_reader_demo_prefers_persisted_account_when_present(db_path, tmp_path, capsys):
    # provision the persisted demo account with seeded history in a COPY of the catalog, then
    # --reader demo must resolve to THAT measured user (real history + relationship analysis),
    # announcing it on stderr — not the synthetic reader.
    import shutil
    dbc = tmp_path / "demo_present.db"
    for suf in ("", "-wal", "-shm"):                  # copy WAL sidecars -> a COMPLETE catalog copy
        src = pathlib.Path(f"{db_path}{suf}")
        if src.exists():
            shutil.copy(src, tmp_path / f"demo_present.db{suf}")
    st = store_mod.Store(f"sqlite:///{dbc}")
    demo = st.upsert_user_by_identity("dev", "demo@infodiet.local", display_name="Demo").id
    for k in range(4):
        url = f"https://ap0.example.com/sbx/{k * 8}"
        st.add_read(demo, er._canon(url),
                    {"article_id": er._canon(url), "outlet": "AP", "category": "Politics",
                     "lean": -1.0, "political": True, "title": f"sbx{k * 8}"},
                    _iso(0.2 + k * 0.1), read_source="notebook")
    code = rec_sandbox.main(["--db", f"sqlite:///{dbc}", "--reader", "demo"])
    cap = capsys.readouterr()
    assert code == 0
    assert f"Resolved persisted demo account (user:{demo})." in cap.err     # the note, on stderr
    assert "3. Reading History" in cap.out and "sbx0" in cap.out            # measured history
    assert "Total reads:" in cap.out and "6. Relationship Analysis" in cap.out
    assert "No persisted reading history exists for this reader" not in cap.out


def test_reader_demo_falls_back_to_synthetic_when_absent(db_path, capsys):
    # no persisted demo account in this catalog -> --reader demo keeps the synthetic reader and
    # says so on stderr; stdout is the unchanged synthetic behaviour.
    code = rec_sandbox.main(["--db", f"sqlite:///{db_path}", "--reader", "demo"])
    cap = capsys.readouterr()
    assert code == 0
    assert "Persisted demo account not found; using synthetic demo reader." in cap.err
    assert "demo reader" in cap.out                                         # synthetic label
    assert "No persisted reading history exists for this reader" in cap.out


def test_persisted_demo_lookup_is_read_only_and_resolution_is_targeted(db_path, tmp_path):
    # the identity lookup is READ-ONLY (a SELECT that never creates the account), and
    # _resolve_demo_readers rewrites ONLY demo readers — user:/row: pass through untouched.
    import shutil
    dbc = tmp_path / "ro.db"
    for suf in ("", "-wal", "-shm"):
        src = pathlib.Path(f"{db_path}{suf}")
        if src.exists():
            shutil.copy(src, tmp_path / f"ro.db{suf}")
    st = store_mod.Store(f"sqlite:///{dbc}")

    def _identity_count():
        with st.session() as s:
            return len(s.scalars(store_mod.select(store_mod.Identity)).all())

    n0 = _identity_count()
    assert rec_sandbox._persisted_demo_user_id(st) is None                  # absent -> None
    assert _identity_count() == n0                                          # lookup created nothing
    uid = st.upsert_user_by_identity("dev", "demo@infodiet.local").id       # explicit create
    assert _identity_count() == n0 + 1
    assert rec_sandbox._persisted_demo_user_id(st) == uid                   # present -> the id
    assert _identity_count() == n0 + 1                                      # lookup created nothing
    readers = [{"kind": "demo"}, {"kind": "user", "id": 5}, {"kind": "row", "row": 2}]
    resolved, note = rec_sandbox._resolve_demo_readers(readers, st)
    assert resolved == [{"kind": "user", "id": uid}, {"kind": "user", "id": 5},
                        {"kind": "row", "row": 2}]                          # only demo rewritten
    assert note == f"Resolved persisted demo account (user:{uid})."
    assert rec_sandbox._resolve_demo_readers([{"kind": "user", "id": 5}], st) == \
        ([{"kind": "user", "id": 5}], None)                                 # no demo -> no note


def test_card_enrichment_resolves_catalog_metadata(store):
    # a catalog URL enriches to its title/category/lean; not the raw publisher/url
    a = rec_sandbox._catalog_article(store, "https://ap0.example.com/sbx/0")
    assert a is not None
    assert a["title"].startswith("sbx0") and a["category"] == "Politics"
    assert a["lean"] is not None


def test_card_enrichment_graceful_fallback(store):
    # a URL absent from the catalog, and a None store, both degrade to None (no crash) —
    # the renderer then falls back to the report's publisher/url
    assert rec_sandbox._catalog_article(store, "https://not-in-catalog.example/x") is None
    assert rec_sandbox._catalog_article(None, "https://ap0.example.com/sbx/0") is None
    # the reader-history helper degrades the same way for synthetic readers / no store
    assert rec_sandbox._reader_history(store, {"kind": "demo"}) is None
    assert rec_sandbox._reader_history(None, {"kind": "user", "id": 1}) is None


def test_cli_presets_are_valid_spec_inputs(db_path, capsys):
    code = rec_sandbox.main(["--db", f"sqlite:///{db_path}", "--preset", "left",
                             "--preset", "duplicate", "--preset", "low_quality", "--json"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["injected"]) == 4                      # left(1) + duplicate(2) + junk(1)
    left = report["injected"][0]
    assert left["disposition"] == "evaluated" and left["scored"]["lean"] is not None
    junk = report["injected"][3]
    assert junk["graphNode"] is False                        # unknown outlet stays un-ranked
