"""Tests for examples/enrich.py — headline enrichment (register + emotion).

Covers the deterministic baseline heuristics, that enrichment flows through the scorer + its
per-URL cache (an article is enriched once), the optional LLM enricher's parse + fallback, and
the payoff: enriched reads make Reporting Ratio + Emotional Balance populate on a real user's
measured report (which stay n/a without enrichment). Offline + deterministic.
"""

import dataclasses
import math
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import enrich                     # noqa: E402
import ingest                     # noqa: E402
import store as store_mod         # noqa: E402


# --------------------------------------------------------------------------- #
# baseline register
# --------------------------------------------------------------------------- #
def test_register_opinion_vs_reporting():
    be = enrich.BaselineEnricher()
    opinion = be.register("Opinion: we must stop ignoring the crisis", category="Opinion")
    reporting = be.register("Senate passes the bill, official says", category="Politics")
    assert 0.05 <= opinion < 0.4                      # opinion cues + Opinion section -> low
    assert reporting > 0.6                            # attribution + event -> high
    assert opinion < reporting
    # bounds always respected
    assert 0.05 <= be.register("x" * 3) <= 0.95


def test_register_is_neutral_ish_for_plain_headline():
    r = enrich.BaselineEnricher().register("City council to meet on Tuesday")
    assert 0.5 <= r <= 0.75                            # near the 0.6 base, no strong cues


# --------------------------------------------------------------------------- #
# baseline emotion
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("headline,dominant", [
    ("Deadly threat warning as crisis looms", "fear"),
    ("Senator slams rival amid furious backlash", "outrage"),
    ("Analysis: what to know about the new data", "analysis"),
    ("Team celebrates historic record win", "positive"),
    ("Council approves the annual budget", "neutral"),
])
def test_emotion_dominant_bucket(headline, dominant):
    emo = enrich.BaselineEnricher().emotion(headline)
    assert set(emo) == set(enrich.LABELS)             # all five labels present
    assert abs(sum(emo.values()) - 1.0) < 1e-6        # a distribution
    assert max(emo, key=emo.get) == dominant


def test_plain_headline_is_fully_neutral():
    emo = enrich.BaselineEnricher().emotion("Council approves the annual budget")
    assert emo["neutral"] == 1.0


# --------------------------------------------------------------------------- #
# scorer integration + determinism + empty title
# --------------------------------------------------------------------------- #
def test_scorer_with_enricher_fills_register_and_emotion():
    scorer = ingest.Scorer(enricher=enrich.BaselineEnricher())
    r = scorer.score(ingest.RawRead(url="https://www.foxnews.com/politics/x",
                                    title="Deadly threat as crisis looms, official warns"))
    assert 0.0 <= r.register <= 1.0 and not math.isnan(r.register)
    assert r.emotion is not None and set(r.emotion) == set(enrich.LABELS)
    assert r.emotion["fear"] > 0                        # 'threat'/'crisis'/'warns'


def test_no_enricher_leaves_register_nan():
    r = ingest.Scorer().score(ingest.RawRead(url="https://www.cnn.com/x", title="A headline"))
    assert math.isnan(r.register) and r.emotion is None   # default scorer is unchanged


def test_empty_headline_stays_na():
    r = ingest.Scorer(enricher=enrich.BaselineEnricher()).score(
        ingest.RawRead(url="https://www.npr.org/x"))       # no title
    assert math.isnan(r.register) and r.emotion is None    # honest: nothing to enrich


def test_enrichment_is_deterministic():
    be = enrich.BaselineEnricher()
    h = "Senator slams rival as deadly crisis looms, study finds"
    assert be.register(h) == be.register(h)
    assert be.emotion(h) == be.emotion(h)


# --------------------------------------------------------------------------- #
# caching: an article is enriched once per canonical URL
# --------------------------------------------------------------------------- #
def test_cached_article_is_not_re_enriched():
    store = store_mod.Store("sqlite:///:memory:")
    calls = {"n": 0}

    class CountingEnricher(enrich.BaselineEnricher):
        def enrich(self, scored, raw):
            calls["n"] += 1
            return super().enrich(scored, raw)

    scorer = ingest.Scorer(enricher=CountingEnricher())
    a = ingest.score_with_cache(ingest.RawRead(url="https://cnn.com/a", title="Crisis warning"), scorer, store)
    b = ingest.score_with_cache(ingest.RawRead(url="https://www.cnn.com/a/", title="Crisis warning"), scorer, store)
    assert calls["n"] == 1                              # same canonical URL -> enriched once
    assert a.register == b.register and a.emotion == b.emotion   # cached enrichment reused


# --------------------------------------------------------------------------- #
# optional LLM enricher: parse + normalize + fallback
# --------------------------------------------------------------------------- #
def test_llm_enricher_parses_and_normalizes():
    def caller(prompt):
        return ('here you go: {"reporting": 0.9, '
                '"emotion": {"fear": 0, "outrage": 0, "analysis": 0, "positive": 0, "neutral": 10}}')
    r = enrich.LLMEnricher(caller).enrich(
        ingest.Scorer().score(ingest.RawRead(url="https://x.com/a", title="A headline")),
        ingest.RawRead(url="https://x.com/a", title="A headline"))
    assert r.register == 0.9
    assert abs(sum(r.emotion.values()) - 1.0) < 1e-6 and r.emotion["neutral"] == 1.0   # normalized


def test_llm_enricher_falls_back_to_baseline_on_error():
    def broken(prompt):
        raise RuntimeError("no key / network")
    raw = ingest.RawRead(url="https://x.com/a", title="Deadly crisis looms")
    base = ingest.Scorer().score(raw)
    r = enrich.LLMEnricher(broken).enrich(base, raw)
    assert not math.isnan(r.register) and r.emotion["fear"] > 0    # baseline result, never fails


def test_make_enricher_factory(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert isinstance(enrich.make_enricher("baseline"), enrich.BaselineEnricher)
    assert enrich.make_enricher("off") is None
    # llm requested but no key -> falls back to the baseline, never None
    assert isinstance(enrich.make_enricher("llm"), enrich.BaselineEnricher)


# --------------------------------------------------------------------------- #
# payoff: enriched reads populate Reporting Ratio + Emotional Balance
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def backend():
    import api_server as engine
    return engine.Backend(engine.DatasetProfile.synthetic(n_users=200, max_items=500, seed=0))


_READS = [
    ("https://www.nytimes.com/2026/us/politics/a", "Senate advances the bill, leaders say"),
    ("https://www.foxnews.com/politics/b", "Outrage as officials slam the deadly crisis"),
    ("https://www.wsj.com/politics/c", "Opinion: we must act now"),
    ("https://www.washingtonpost.com/politics/d", "Analysis: what to know about the vote"),
    ("https://www.theguardian.com/us-news/politics/e", "Hope as historic deal celebrated"),
    ("https://apnews.com/hub/politics/f", "Poll finds shifting views, data shows"),
]


def _measured(backend, enricher):
    import personalize
    st = store_mod.Store("sqlite:///:memory:")
    scorer = ingest.Scorer(enricher=enricher)
    uid = st.upsert_user_by_identity("google", f"enrich-{id(enricher)}").id
    for url, title in _READS:
        sr = ingest.score_with_cache(ingest.RawRead(url=url, title=title), scorer, st)
        st.add_read(uid, sr.article_id, dataclasses.asdict(sr), sr.read_at)
    p = personalize.Personalizer(backend, st, persist=False)
    return p.report(uid)


def test_enrichment_populates_reporting_and_emotional(backend):
    plain = {m["key"] for m in _measured(backend, None)["metrics"]}          # no enricher
    enriched = {m["key"] for m in _measured(backend, enrich.BaselineEnricher())["metrics"]}
    assert "reportingRatio" not in plain and "emotionalBalance" not in plain  # n/a without enrichment
    assert {"reportingRatio", "emotionalBalance"} <= enriched                 # populated with it


def test_measured_report_is_deterministic_with_enrichment(backend):
    a = _measured(backend, enrich.BaselineEnricher())
    b = _measured(backend, enrich.BaselineEnricher())
    strip = lambda d: {k: v for k, v in d.items() if k != "updatedAt"}
    assert strip(a) == strip(b)                          # identical (minus timestamp)
