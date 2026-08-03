"""Tests for examples/benchmark_insights.py — the provider/model benchmark harness.

The harness's value depends on two claims, and these tests pin both: it measures the PRODUCTION
pipeline (so a validation failure in the report is a real refusal by the real validator), and it
selects targets purely through the environment (so adding a model is configuration). Everything
runs against a live local server speaking Ollama's protocol — no vendor SDK, no network.
"""

import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import article_insights              # noqa: E402
import benchmark_insights as bench   # noqa: E402

GOOD = {"summary": "First sentence of the summary. Second sentence of the summary.",
        "bias": {"framing": "Foregrounds the official account.", "tone": "Measured.",
                 "loadedLanguage": ["crackdown"], "omissions": "No costs given.",
                 "viewpoint": "Centres officials."}}
ONE_SENTENCE = {**GOOD, "summary": "Only one sentence here."}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self._send(200, {"models": [{"name": "t"}]}) if self.path == "/api/tags" \
            else self._send(404, {"error": "no"})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"] or 0)) or b"{}")
        # "bad-model" answers with a 1-sentence summary the production validator must refuse.
        payload = ONE_SENTENCE if "bad" in (req.get("model") or "") else GOOD
        self._send(200, {"message": {"content": json.dumps(payload)},
                         "prompt_eval_count": 321, "eval_count": 123})

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def articles():
    return bench.load_articles(bench.GOLDEN_SET, ["council", "brief"])


def _target(name, model, host, **kw):
    return bench.Target(name=name, provider="ollama", model=model,
                        env={"OLLAMA_HOST": host}, **kw)


def test_shipped_config_files_load_and_are_coherent():
    arts = bench.load_articles(bench.GOLDEN_SET)
    assert len(arts) >= 8
    assert len({a["id"] for a in arts}) == len(arts)              # ids unique
    for a in arts:                                                 # every article is usable
        assert a["headline"] and a.get("description")
        assert a["probes"]
    targets = bench.load_targets(bench.TARGETS)
    assert {t.provider for t in targets} <= set(bench.insights_provider._REGISTRY)
    assert len({t.name for t in targets}) == len(targets)


def test_selection_is_env_only_and_restores_the_environment(server, monkeypatch):
    monkeypatch.setenv("RWE_INSIGHTS_PROVIDER", "anthropic")
    monkeypatch.setenv("SENTINEL", "untouched")
    t = _target("x", "some-model", server)
    with bench._env_for(t):
        import os
        assert os.environ["RWE_INSIGHTS_PROVIDER"] == "ollama"
        assert os.environ["RWE_INSIGHTS_MODEL"] == "some-model"
        assert os.environ["OLLAMA_HOST"] == server
    import os
    assert os.environ["RWE_INSIGHTS_PROVIDER"] == "anthropic"     # restored
    assert os.environ["SENTINEL"] == "untouched"


def test_run_measures_the_production_validator_not_a_copy(server, articles):
    """A model whose answer breaks the 2-4 sentence bound must be counted as a validation
    failure — which can only happen if the harness is calling the real validator."""
    good = bench.run_target(_target("good", "fine-model", server), articles, repeats=1)
    bad = bench.run_target(_target("bad", "bad-model", server), articles, repeats=1)
    sg, sb = bench.summarize(good), bench.summarize(bad)
    assert sg["pass_rate"] == 1.0 and sg["validation_fail"] == 0
    assert sb["pass_rate"] == 0.0 and sb["validation_fail"] == len(articles)
    assert sb["transport_fail"] == 0                               # the vendor answered fine
    assert "2-4 sentences" in sb["failures"][0]["reason"]


def test_token_usage_is_captured_from_the_vendor_envelope(server, articles):
    s = bench.summarize(bench.run_target(_target("m", "fine-model", server), articles, repeats=1))
    assert s["measured_tokens"] is True
    assert s["avg_in"] == 321 and s["avg_out"] == 123              # the server's own counts


def test_cost_per_1k_uses_measured_tokens_and_the_configured_price(server, articles):
    t = _target("priced", "fine-model", server,
                pricing={"input_per_mtok": 5.0, "output_per_mtok": 25.0})
    s = bench.summarize(bench.run_target(t, articles, repeats=1))
    expected = ((321 * 5.0 + 123 * 25.0) / 1e6) * 1000
    assert s["cost_1k"] == pytest.approx(expected)
    assert bench._fmt_cost(s) == f"${expected:.2f}"                 # no "(est)" — measured
    free = bench.summarize(bench.run_target(_target("free", "fine-model", server), articles,
                                            repeats=1))
    assert bench._fmt_cost(free) == "$0.00 (local)"


def test_an_unavailable_provider_is_reported_not_raised(articles, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run = bench.run_target(bench.Target(name="hosted", provider="anthropic", model="m"),
                           articles, repeats=1)
    assert run["calls"] == [] and "ANTHROPIC_API_KEY" in run["skipped"]
    assert bench.summarize(run)["skipped"]


def _section(suite, targets_and_models, articles=None, repeats=1):
    arts = articles if articles is not None else suite.articles
    runs = [bench.run_target(t, arts, repeats=repeats) for t in targets_and_models]
    return {"suite": suite, "runs": runs, "summaries": [bench.summarize(r) for r in runs]}


def test_report_contains_the_sections_the_reader_needs(server, articles):
    suite = bench.Suite("golden", "regression", articles, "Fixed synthetic set.")
    sec = _section(suite, [_target("good", "fine-model", server),
                           _target("bad", "bad-model", server),
                           bench.Target(name="hosted", provider="anthropic")])
    md = bench.report_markdown([sec], repeats=1, note="unit test")
    for needle in ("### Results", "pass rate", "### Failure breakdown", "### Samples",
                   "## Method & caveats", "unit test", "tokens in/out",
                   "est. cost / 1k articles", "Suite `golden`", "regression"):
        assert needle in md, needle
    assert "skipped" in md                                         # the hosted target
    assert "✗ rejected" in md                                      # the refused artifact shows
    assert GOOD["summary"] in md                                   # verbatim sample text


# ------------------------------------------------------------------ #
# --sample-production: the realism suite
# ------------------------------------------------------------------ #

@pytest.fixture()
def prod_db(tmp_path):
    """A stand-in production catalog: real store, real rows, one with contact details."""
    import store as store_mod
    db = f"sqlite:///{tmp_path}/prod.db"
    st = store_mod.Store(db)
    body = "A sentence of ordinary catalog copy that clears the floor. " * 6
    for i in range(12):
        url = f"https://outlet{i}.example.com/a/{i}"
        st.upsert_feed_article(
            canonical_url=url, url=url, publisher=f"Outlet {i}", source_publisher=None,
            title=f"Production headline {i} about a distinct municipal matter",
            description=body, body=None, published_at="2026-08-02T00:00:00Z", source_feed="f",
            scored={"article_id": url, "outlet": f"Outlet {i}", "category": "Politics",
                    "lean": 0.0, "political": True, "title": f"h{i}"})
    leaky = "https://outlet-pii.example.com/a/99"
    st.upsert_feed_article(
        canonical_url=leaky, url=leaky, publisher="Leaky Gazette", source_publisher=None,
        title="Residents told to contact the office", description=body + " Write to a.b@x.org.",
        body=None, published_at="2026-08-02T00:00:00Z", source_feed="f",
        scored={"article_id": leaky, "outlet": "Leaky Gazette", "category": "Politics",
                "lean": 0.0, "political": True, "title": "pii"})
    # A stub too short to be worth a call — the eligibility floor must exclude it.
    stub = "https://outlet-stub.example.com/a/1"
    st.upsert_feed_article(
        canonical_url=stub, url=stub, publisher="Stub Wire", source_publisher=None,
        title="Short", description="Tiny.", body=None, published_at="2026-08-02T00:00:00Z",
        source_feed="f", scored={"article_id": stub, "outlet": "Stub Wire",
                                 "category": "Politics", "lean": 0.0, "political": True,
                                 "title": "s"})
    return db


def test_production_sample_is_reproducible_and_respects_the_floor(prod_db):
    a = bench.sample_production(5, seed=7, db=prod_db)
    b = bench.sample_production(5, seed=7, db=prod_db)
    c = bench.sample_production(5, seed=8, db=prod_db)
    assert a.kind == "realism" and a.name == "production"
    assert len(a.articles) == 5
    ids = [x["id"] for x in a.articles]
    assert ids == [x["id"] for x in b.articles]                    # same seed, same sample
    assert ids != [x["id"] for x in c.articles]                    # different seed, different
    assert all(x["id"].startswith("prod-") for x in a.articles)    # opaque, stable ids
    # the stub never appears: it cannot clear the eligibility floor
    everything = bench.sample_production(99, seed=1, db=prod_db)
    assert all("Short" not in x["headline"] for x in everything.articles)
    assert all(article_insights.eligible(x) for x in everything.articles)


def test_production_articles_reach_the_model_unmodified(prod_db):
    """The realism claim: the text handed to the model is the stored text, byte for byte."""
    import store as store_mod
    from sqlalchemy import select
    suite = bench.sample_production(3, seed=3, db=prod_db)
    st = store_mod.Store(prod_db)
    with st._Session() as s:
        stored = dict(s.execute(select(store_mod.FeedArticle.title,
                                       store_mod.FeedArticle.description)).all())
    assert suite.articles
    for art in suite.articles:
        assert art["headline"] in stored
        assert art["description"] == stored[art["headline"]]


def test_sampling_writes_nothing_back(prod_db, server, monkeypatch):
    """Read-only: benchmarking a production sample must not create insights rows."""
    import store as store_mod
    from sqlalchemy import func, select
    st = store_mod.Store(prod_db)

    def count():
        with st._Session() as s:
            return s.execute(select(func.count()).select_from(store_mod.ArticleInsight)).scalar()

    before = count()
    suite = bench.sample_production(4, seed=2, db=prod_db)
    bench.run_target(_target("m", "fine-model", server), suite.articles, repeats=1)
    assert count() == before == 0


def test_anonymisation_is_proportionate(prod_db):
    """Off by default (the text is published journalism), forced by --anonymize, and forced for
    an article carrying contact details whatever the flag says."""
    plain = bench.sample_production(99, seed=1, db=prod_db)
    leaky = next(a for a in plain.articles if a["_auto_anonymised"])
    ordinary = next(a for a in plain.articles if not a["_auto_anonymised"])
    assert ordinary["_display_headline"] == ordinary["headline"]          # not scrubbed
    assert ordinary["_display_publisher"].startswith("Outlet")
    assert leaky["_display_headline"] == leaky["id"]                      # auto-redacted
    assert leaky["_display_publisher"] == "(withheld)"
    assert "@" in leaky["description"]          # …but the model still sees the real text
    assert "auto-anonymised" in plain.note

    hidden = bench.sample_production(99, seed=1, db=prod_db, anonymize=True)
    assert all(a["_display_headline"] == a["id"] for a in hidden.articles)
    assert all(a["_display_publisher"] == "(withheld)" for a in hidden.articles)


def test_report_never_reproduces_article_bodies(prod_db, server):
    suite = bench.sample_production(3, seed=5, db=prod_db)
    sec = _section(suite, [_target("m", "fine-model", server)])
    md = bench.report_markdown([sec], repeats=1)
    for art in suite.articles:
        assert art["description"][:60] not in md          # body text stays out of the report
        assert art["id"] in md                            # the opaque id is how it is referenced
    assert "Suite `production`" in md and "realism" in md


def test_both_suites_render_side_by_side(prod_db, server, articles):
    golden = bench.Suite("golden", "regression", articles, "Fixed synthetic set.")
    prod = bench.sample_production(3, seed=5, db=prod_db)
    t = [_target("m", "fine-model", server)]
    md = bench.report_markdown([_section(golden, t), _section(prod, t)], repeats=1)
    assert "Suite `golden`" in md and "Suite `production`" in md
    assert "Two suites, two jobs" in md                    # the framing the reader needs
    assert md.index("Suite `golden`") < md.index("Suite `production`")


def test_cli_runs_both_suites_and_labels_them_in_json(server, prod_db, tmp_path, monkeypatch):
    cfg = tmp_path / "t.json"
    cfg.write_text(json.dumps({"version": 1, "targets": [
        {"name": "local/fine", "provider": "ollama", "model": "fine-model",
         "env": {"OLLAMA_HOST": server}, "pricing": {}, "notes": ""}]}))
    out_json = tmp_path / "r.json"
    assert bench.main(["--targets-file", str(cfg), "--articles", "council", "--quiet",
                       "--sample-production", "2", "--seed", "4", "--db", prod_db,
                       "--json", str(out_json), "--out", str(tmp_path / "r.md")]) == 0
    raw = json.loads(out_json.read_text())
    assert {r["suite"] for r in raw} == {"golden", "production"}
    assert {r["kind"] for r in raw} == {"regression", "realism"}


def test_skip_golden_requires_a_sample(tmp_path):
    cfg = tmp_path / "t.json"
    cfg.write_text(json.dumps({"version": 1, "targets": []}))
    with pytest.raises(SystemExit, match="--skip-golden"):
        bench.main(["--targets-file", str(cfg), "--skip-golden"])


def test_cli_list_and_a_full_run_work_end_to_end(server, tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "targets.json"
    cfg.write_text(json.dumps({"version": 1, "targets": [
        {"name": "local/fine", "provider": "ollama", "model": "fine-model",
         "env": {"OLLAMA_HOST": server}, "pricing": {}, "notes": "n"}]}))
    assert bench.main(["--list", "--targets-file", str(cfg)]) == 0
    assert "local/fine" in capsys.readouterr().out
    out_md, out_json = tmp_path / "r.md", tmp_path / "r.json"
    assert bench.main(["--targets-file", str(cfg), "--articles", "council", "--quiet",
                       "--out", str(out_md), "--json", str(out_json)]) == 0
    assert "# Article Insights — provider/model benchmark" in out_md.read_text()
    raw = json.loads(out_json.read_text())
    assert raw[0]["target"] == "local/fine" and raw[0]["calls"][0]["ok"] is True
