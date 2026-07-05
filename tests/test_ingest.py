"""Unit tests for examples/ingest.py (Milestone C1) — the reading-event scorer + cache.

Deterministic + offline: the baseline scorer uses only the bundled outlet-lean table, and the
cache is the SQLite store. Verifies the scorer produces the B4 ``ScoredRead`` interface (so it
plugs straight into the augmented corpus), and that a URL is scored once and reused. No
endpoint is exercised — C1 is not wired into the product.
"""

import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import augmented_corpus as ac   # noqa: E402
import ingest                   # noqa: E402
import store as store_mod       # noqa: E402


def test_canonical_url():
    c = ingest.canonical_url("https://WWW.NYTimes.com/2024/us/politics/x/?utm=1#top")
    assert c == "https://nytimes.com/2024/us/politics/x"


def test_scorer_baseline_known_outlet():
    r = ingest.Scorer().score(ingest.RawRead(
        url="https://www.nytimes.com/2024/01/05/us/politics/story.html", title="A story"))
    assert r.outlet == "New York Times"            # canonical outlet from the registry (was "nytimes.com")
    assert r.lean == -1.0                          # NYT AllSides lean
    assert r.political is True                      # "politic" in the path
    assert r.category in {"U.S.", "Politics"}       # coarse section heuristic
    assert r.article_id == "https://nytimes.com/2024/01/05/us/politics/story.html"
    assert r.emotion is None and np.isnan(r.register)   # no enricher -> stays n/a


def test_scorer_unknown_outlet_is_nan_lean():
    r = ingest.Scorer().score(ingest.RawRead(url="https://some-local-blog.example/post"))
    assert r.outlet == "some-local-blog.example"   # bare domain, still ingests
    assert np.isnan(r.lean)
    assert r.political is False


# --------------------------------------------------------------------------- #
# Registry wiring (Milestone C-registry/2): outlets now resolve through the canonical
# OutletRegistry, so previously-broken outlets get the right identity + lean, and unknown
# outlets still ingest with NaN lean. See tests/test_outlet_registry.py for the registry itself.
# --------------------------------------------------------------------------- #
def test_previously_broken_outlets_now_resolve():
    """Regression: the old scorer dropped these to NaN lean — via the lstrip('www.') bug
    (washingtonpost/wsj) or missing-from-the-table (CNBC, Al Jazeera, LA Times, and the
    domain/name split on AP / Guardian). All must resolve to canonical name + AllSides lean."""
    scorer = ingest.Scorer()
    cases = [
        ("https://www.washingtonpost.com/2026/politics/a", "Washington Post", -1.0),
        ("https://www.wsj.com/business/b",                 "Wall Street Journal", 1.0),
        ("https://apnews.com/article/c",                   "Associated Press", 0.0),
        ("https://www.theguardian.com/us-news/d",          "The Guardian", -1.0),
        ("https://www.cnbc.com/2026/markets/e",            "CNBC", -1.0),
        ("https://www.aljazeera.com/news/f",               "Al Jazeera", -1.0),
        ("https://www.latimes.com/politics/g",             "Los Angeles Times", -1.0),
    ]
    for url, canonical, lean in cases:
        r = scorer.score(ingest.RawRead(url=url))
        assert r.outlet == canonical, f"{url} -> {r.outlet!r}"
        assert r.lean == lean, f"{url} lean {r.lean}"


def test_www_prefix_handled_on_both_paths():
    """The specific bug: lstrip('www.') corrupted leading 'w'. Known w-outlets resolve via the
    registry; an *unknown* w-outlet falls back to the correct bare domain (not 'ubblog.example')."""
    scorer = ingest.Scorer()
    assert scorer.score(ingest.RawRead(url="https://www.washingtonpost.com/x")).lean == -1.0
    assert scorer.score(ingest.RawRead(url="https://www.wsj.com/x")).lean == 1.0
    r = scorer.score(ingest.RawRead(url="https://www.wubblog.example/x"))
    assert r.outlet == "wubblog.example" and np.isnan(r.lean)


def test_unknown_outlet_ingests_successfully():
    """Unknown outlets must not fail ingestion — bare domain, NaN lean, still a valid read."""
    r = ingest.Scorer().score(ingest.RawRead(url="https://indie-daily.example/story", title="t"))
    assert r.outlet == "indie-daily.example"
    assert np.isnan(r.lean)
    assert r.article_id == "https://indie-daily.example/story" and r.title == "t"


def test_source_fields_override_heuristics():
    r = ingest.Scorer().score(ingest.RawRead(
        url="https://foxnews.com/a", outlet="Fox News", category="Science", political=False))
    assert r.category == "Science" and r.political is False
    assert r.lean == 2.0                            # Fox News in the table


def test_enricher_is_applied_when_present():
    class Emo:
        def enrich(self, scored, raw):
            from dataclasses import replace
            return replace(scored, register=0.6, emotion={
                "fear": 0.1, "outrage": 0.1, "analysis": 0.5, "positive": 0.2, "neutral": 0.1})

    r = ingest.Scorer(enricher=Emo()).score(ingest.RawRead(url="https://npr.org/x"))
    assert r.emotion is not None and r.register == 0.6


def test_score_with_cache_scores_once():
    store = store_mod.Store("sqlite:///:memory:")
    calls = {"n": 0}

    class CountingScorer(ingest.Scorer):
        def score(self, raw):
            calls["n"] += 1
            return super().score(raw)

    scorer = CountingScorer()
    a = ingest.score_with_cache(ingest.RawRead(url="https://cnn.com/a?x=1", read_at="t1"), scorer, store)
    b = ingest.score_with_cache(ingest.RawRead(url="https://www.cnn.com/a/#frag", read_at="t2"), scorer, store)
    assert calls["n"] == 1                          # same canonical URL -> scored once
    assert a.article_id == b.article_id
    assert a.lean == b.lean and a.outlet == b.outlet
    assert b.read_at == "t2"                        # the read's own timestamp is preserved


def test_normalize_and_has_host():
    assert ingest.normalize_url("  nytimes.com/a ") == "https://nytimes.com/a"
    assert ingest.normalize_url("https://x.com/a") == "https://x.com/a"
    assert ingest.has_host("https://nytimes.com/a") is True
    assert ingest.has_host("https://not a host") is False   # space
    assert ingest.has_host("https://justtext") is False     # no dot
    assert ingest.has_host("") is False


def test_scored_reads_match_the_b4_interface():
    import dataclasses
    scorer = ingest.Scorer()
    reads = [scorer.score(ingest.RawRead(url=u)) for u in (
        "https://nytimes.com/us/politics/a", "https://foxnews.com/politics/b",
        "https://npr.org/world/c")]
    # Field-name check (not isinstance): sibling tests load augmented_corpus via importlib, so
    # the class object can differ while the ScoredRead interface is identical.
    expected = {f.name for f in dataclasses.fields(ac.ScoredRead)}
    for x in reads:
        assert type(x).__name__ == "ScoredRead"
        assert {f.name for f in dataclasses.fields(type(x))} == expected
        assert isinstance(x.article_id, str) and x.article_id
