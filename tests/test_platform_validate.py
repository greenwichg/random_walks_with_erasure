"""examples/platform_validate.py — the battery the operator runs after enabling ``/v1``.

Driven in local mode over a store seeded by the real ingest path (the same fixture shape
``test_platform_api.py`` uses), so every section runs: the capability checks, the exposure sweep
with a KNOWN hidden row (a reader-private extension observation), the quality measurements and the
latency table. One test proves the sweep catches a leak when a payload carries a hidden reference.
"""

import json
import pathlib
import subprocess
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import platform_validate  # noqa: E402
import rss_ingest  # noqa: E402
import store as store_mod  # noqa: E402
import story_service  # noqa: E402

E = rss_ingest.FeedEntry


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("RWE_PLATFORM_API", "1")
    monkeypatch.delenv("RWE_PLATFORM_PUBLISH_RATINGS", raising=False)
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "36500")
    monkeypatch.setenv("RWE_OUTLET_INDEX_DB", str(tmp_path / "idx.db"))
    story_service.clear_cache()
    yield
    story_service.clear_cache()


def _seed(db_url: str):
    st = store_mod.Store(db_url)
    scorer = rss_ingest.make_scorer()
    rss_ingest.ingest_entries([
        E(url="https://www.bbc.co.uk/news/articles/abc123", title="Prime minister resigns after vote",
          published_at="2026-09-01T10:00:00+00:00", description="A long day in Westminster. " * 30,
          publisher_hint="bbc.co.uk"),
        E(url="https://www.theguardian.com/politics/2026/sep/01/pm-resigns",
          title="Prime minister resigns after confidence vote", published_at="2026-09-01T11:00:00+00:00",
          publisher_hint="theguardian.com"),
    ], "BBC", "https://feeds.bbci.co.uk/news/rss.xml", scorer, st, source_type="rss")
    rss_ingest.ingest_entries([
        E(url="https://www.npr.org/2026/09/01/pm-resigns-vote", title="Prime minister resigns after losing vote",
          published_at="2026-09-01T13:00:00+00:00", publisher_hint="npr.org",
          source_type="newsapi", source_provider="NewsAPI"),
    ], None, "newsapi", scorer, st, source_type="newsapi")
    rss_ingest.ingest_entries([
        E(url="https://www.npr.org/2026/09/01/only-one-reader-saw-this", title="Prime minister resigns: what we know",
          published_at="2026-09-01T14:00:00+00:00", publisher_hint="npr.org", source_type="extension"),
    ], None, "extension", scorer, st, source_type="extension")
    return st


def test_local_battery_runs_every_section_and_passes(tmp_path):
    db = f"sqlite:///{tmp_path}/v.db"
    _seed(db)
    lines = []
    out = platform_validate.run_local(db, backfill=True, repeat=1, log=lines.append)
    assert out["summary"]["FAIL"] == 0, [c for c in out["checks"] if c["level"] == "FAIL"]
    names = {c["name"] for c in out["checks"]}
    for needle in ("internal key resolves", "developer key resolves", "story detail answers", "similar answers",
                   "intelligence answers", "comparison answers", "history answers", "tags vocabulary answers",
                   "search answers", "publishers list answers", "countries answer", "no key -> 401 unauthenticated",
                   "no article body anywhere", "no known-hidden reference",
                   "developer key never receives a restricted row's delivery", "meter counts this run's requests"):
        assert needle in names, needle
    m = out["metrics"]
    assert m["stories"]["onPage"] == 1 and m["coverageClasses"] == {"metadata_public": 2, "provider_restricted": 1}
    assert m["exposure"]["restrictedRowsSeenByDeveloperKey"] >= 1 and m["exposure"]["restrictedDelivery"] == 0
    assert out["mode"]["hiddenRows"] == 2 and m["latencyMs"]["stories"]["n"] >= 1
    assert m["metering"]["meteredThisRun"] == m["metering"]["sentThisRun"] and m["metering"]["recordErrors"] == 0
    # the temporary keys are gone
    st = store_mod.Store(db)
    assert all(k["revokedAt"] for k in st.platform_list_keys("platform-validate"))
    assert any("validation:" in ln for ln in lines)


def test_exposure_sweep_catches_a_leaked_hidden_reference():
    class Fake:                                 # a client whose payload carries a hidden url
        def get(self, path, params=None, headers=None):
            body = {"data": [{"articleId": "ar_hidden", "url": "https://x.example/private"}],
                    "meta": {"ratingsPublished": False}}
            return platform_validate.Resp(200, body, 1.0, {})
    b = platform_validate.Battery(Fake(), key="k", key_dev=None, repeat=1,
                                  hidden_refs={"urls": {"https://x.example/private"}, "ids": {"ar_hidden"}},
                                  log=lambda s: None)
    b.get("articles", "/v1/articles")
    b.s_exposure()
    failed = {c["name"] for c in b.checks if c["level"] == "FAIL"}
    assert "no known-hidden reference" in failed
    assert b.metrics["exposure"]["hiddenRef"] == 2


def test_cli_writes_a_report_and_exit_code(tmp_path):
    db = f"sqlite:///{tmp_path}/cli.db"
    _seed(db)
    out = tmp_path / "report.json"
    rc = platform_validate.main(["--db", db, "--repeat", "1", "--json", str(out), "--quiet"])
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["summary"]["FAIL"] == 0 and report["samples"]["me"]["plan"] == "internal"
    assert "hv_live_" not in out.read_text()          # keys never reach the report


def test_enable_script_and_caddy_route_are_in_place():
    src = (ROOT / "deploy" / "ops" / "platform-enable.sh").read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(ROOT / "deploy" / "ops" / "platform-enable.sh")]).returncode == 0
    for needle in ("RWE_PLATFORM_API=1", "identity_backfill.py --dry-run", "platform_validate.py --base-url",
                   "platform_revoke_key", "caddy reload"):
        assert needle in src, needle
    assert "echo \"$KEY" not in src and "echo $KEY" not in src      # a key is never printed
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert "@platform path /v1/*" in caddy and "reverse_proxy api:8000" in caddy
    assert caddy.index("handle @platform") < caddy.index("reverse_proxy web:3000")
