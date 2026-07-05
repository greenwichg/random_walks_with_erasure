"""Tests for examples/prepare_qbias.py (Commit 3) — Qbias outlet canonicalization.

Verifies the preprocessor rewrites Qbias's outlet vocabulary (incl. parenthetical and all-caps
forms) to the SAME canonical names the ingestion scorer emits, keeps Qbias's schema so the
existing corpus builder consumes it unchanged, leaves unknown outlets in place (no article
dropped), and reports accurate stats. The key property — ingestion outlet names == canonicalized
Qbias outlet names — is asserted directly. Small in-memory fixture; no network, no 14 MB download.
"""

import csv
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import prepare_qbias as pq        # noqa: E402
import ingest                     # noqa: E402

# A miniature Qbias-shaped dataset: its own outlet vocabulary (parenthetical suffixes, all-caps,
# a plain name, a domain form) plus one outlet the registry doesn't know.
FIXTURE_ROWS = [
    {"heading": "A", "source": "Fox News (Online News)", "bias_rating": "right", "tags": "politics"},
    {"heading": "B", "source": "CNN (Online News)", "bias_rating": "left", "tags": "politics"},
    {"heading": "C", "source": "New York Times (News)", "bias_rating": "left", "tags": "world"},
    {"heading": "D", "source": "Wall Street Journal (News)", "bias_rating": "right", "tags": "business"},
    {"heading": "E", "source": "USA TODAY", "bias_rating": "center", "tags": "us"},
    {"heading": "F", "source": "Washington Post", "bias_rating": "left", "tags": "politics"},
    {"heading": "G", "source": "npr.org", "bias_rating": "left", "tags": "world"},          # domain form
    {"heading": "H", "source": "Some Indie Blog", "bias_rating": "center", "tags": "local"}, # unknown
]
_FIELDS = ["heading", "source", "bias_rating", "tags"]


def _write_csv(path, rows, fields=_FIELDS):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_canonicalize_maps_qbias_forms_to_canonical():
    rows, rep = pq.canonicalize_rows(FIXTURE_ROWS, "source")
    got = [r["source"] for r in rows]
    assert got == [
        "Fox News", "CNN", "New York Times", "Wall Street Journal",
        "USA Today", "Washington Post", "NPR", "Some Indie Blog",  # unknown kept as-is
    ]


def test_report_stats_are_accurate():
    _, rep = pq.canonicalize_rows(FIXTURE_ROWS, "source")
    assert rep.articles_total == 8
    assert rep.articles_matched == 7                     # all but "Some Indie Blog"
    assert rep.pct_articles_canonicalized == pytest.approx(87.5)
    assert dict(rep.unmatched) == {"Some Indie Blog": 1}
    assert "Fox News" in rep.canonical_outlets and "NPR" in rep.canonical_outlets
    assert len(rep.sources_matched) == 7                 # 7 distinct raw sources resolved


def test_prepare_preserves_schema_and_rowcount(tmp_path):
    raw = tmp_path / "raw.csv"
    clean = tmp_path / "clean.csv"
    _write_csv(raw, FIXTURE_ROWS)
    pq.prepare(str(raw), str(clean))
    with open(clean, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        assert rd.fieldnames == _FIELDS                  # schema unchanged
        out = list(rd)
    assert len(out) == len(FIXTURE_ROWS)                 # no row dropped
    assert out[0]["source"] == "Fox News" and out[0]["bias_rating"] == "right"  # only outlet rewritten


def test_ingestion_and_qbias_outlet_names_align():
    """The whole point: a reader's URL and the corresponding Qbias source resolve to the SAME
    canonical outlet, so real reads merge with the population's outlet buckets."""
    rows, _ = pq.canonicalize_rows(FIXTURE_ROWS, "source")
    corpus_outlet = {r["heading"]: r["source"] for r in rows}
    scorer = ingest.Scorer()

    def read_outlet(url):
        return scorer.score(ingest.RawRead(url=url)).outlet

    assert read_outlet("https://www.foxnews.com/x") == corpus_outlet["A"] == "Fox News"
    assert read_outlet("https://edition.cnn.com/x") == corpus_outlet["B"] == "CNN"
    assert read_outlet("https://www.nytimes.com/x") == corpus_outlet["C"] == "New York Times"
    assert read_outlet("https://www.wsj.com/x") == corpus_outlet["D"] == "Wall Street Journal"
    assert read_outlet("https://www.usatoday.com/x") == corpus_outlet["E"] == "USA Today"
    assert read_outlet("https://www.washingtonpost.com/x") == corpus_outlet["F"] == "Washington Post"


def test_unknown_outlet_column_raises(tmp_path):
    raw = tmp_path / "bad.csv"
    _write_csv(raw, [{"headline": "x", "provider_x": "Y", "bias": "left"}],
               fields=["headline", "provider_x", "bias"])
    with pytest.raises(SystemExit):
        pq.prepare(str(raw), None)


def test_pick_outlet_column_is_case_insensitive():
    assert pq.pick_outlet_column(["Heading", "Source", "Bias_Rating"]) == "Source"
    assert pq.pick_outlet_column(["a", "b"]) is None


def test_cleaned_csv_is_consumed_by_the_corpus_builder(tmp_path):
    """simulate_users.catalog_from_qbias (unchanged research code) reads the cleaned CSV and now
    sees canonical outlet names — the corpus and ingestion share one outlet vocabulary."""
    su = pytest.importorskip("simulate_users")
    clean = tmp_path / "clean.csv"
    pq.prepare_written = pq.prepare(str(_written_fixture(tmp_path)), str(clean))
    cat = su.catalog_from_qbias(str(clean), seed=0)
    outlets = set(cat.outlets.tolist())
    assert "Fox News" in outlets and "New York Times" in outlets      # canonical, not "foxnews.com"
    assert "Some Indie Blog" in outlets                               # unknown preserved
    assert "Fox News (Online News)" not in outlets                    # raw form gone


def _written_fixture(tmp_path):
    raw = tmp_path / "raw.csv"
    _write_csv(raw, FIXTURE_ROWS)
    return raw


# --------------------------------------------------------------------------- #
# Population/read enrichment alignment: prepare_qbias baseline-enriches each headline into
# register/emotion sidecars (the SAME enricher ingested reads use), keyed to the corpus ids, and
# the engine loads them so the population and real reads share one enrichment pipeline.
# --------------------------------------------------------------------------- #
import enrich   # noqa: E402

# A headline-rich, viable-size Qbias fixture so catalog_from_qbias + a Backend can build.
_HEADLINES = [
    "Officials warn of deadly threat as crisis looms",       # fear
    "Senator slams rival amid furious backlash",              # outrage
    "Team celebrates historic record win",                   # positive
    "Analysis: what to know about the new data",              # analysis
    "City council approves the annual budget",               # neutral
    "Opinion: we must act now on the economy",               # opinion (low register)
    "Report finds shifting views, new poll says",            # reporting (high register)
]
_QSOURCES = ["Fox News (Online News)", "CNN (Online News)", "New York Times (News)",
             "Washington Post", "NPR (Online News)", "Reuters"]
_QBIAS = ["right", "left", "left", "left", "center", "center"]


def _make_qbias_fixture(path, n=140):
    rows = [{"heading": _HEADLINES[i % len(_HEADLINES)], "source": _QSOURCES[i % len(_QSOURCES)],
             "bias_rating": _QBIAS[i % len(_QBIAS)], "tags": "politics"} for i in range(n)]
    _write_csv(path, rows)


def test_write_enrichment_sidecars_match_the_baseline(tmp_path):
    _make_qbias_fixture(tmp_path / "raw.csv", n=14)
    rep = pq.prepare(str(tmp_path / "raw.csv"), str(tmp_path / "clean.csv"))
    assert rep.enriched_articles == 14 and rep.register_path and rep.emotion_path
    be = enrich.BaselineEnricher()
    # register sidecar: news_id,reporting ; keyed Q{i}; value == baseline over the headline
    with open(rep.register_path, newline="", encoding="utf-8") as f:
        reg = {r["news_id"]: float(r["reporting"]) for r in csv.DictReader(f)}
    with open(rep.emotion_path, newline="", encoding="utf-8") as f:
        emo = {r["news_id"]: r for r in csv.DictReader(f)}
    assert set(reg) == {f"Q{i}" for i in range(14)}
    for i, h in [(i, _HEADLINES[i % len(_HEADLINES)]) for i in range(14)]:
        assert reg[f"Q{i}"] == pytest.approx(be.register(h), abs=1e-4)
        assert float(emo[f"Q{i}"]["fear"]) == pytest.approx(be.emotion(h)["fear"], abs=1e-4)
    # sidecar columns match what health_report._load_item_csv expects
    assert list(csv.DictReader(open(rep.emotion_path)).fieldnames) == ["news_id", *enrich.LABELS]


def test_sidecars_align_to_catalog_ids(tmp_path):
    """The sidecar's Q{i} keys line up with the ids simulate_users.catalog_from_qbias assigns."""
    su = pytest.importorskip("simulate_users")
    import health_report as hr
    _make_qbias_fixture(tmp_path / "raw.csv", n=140)
    rep = pq.prepare(str(tmp_path / "raw.csv"), str(tmp_path / "clean.csv"))
    cat = su.catalog_from_qbias(str(tmp_path / "clean.csv"), seed=0)
    reg = hr._load_item_csv(rep.register_path, cat.ids)["reporting"]   # aligned to catalog ids
    be = enrich.BaselineEnricher()
    import numpy as np
    finite = np.isfinite(reg)
    assert finite.sum() > 0
    for col in np.flatnonzero(finite)[:20]:
        assert reg[col] == pytest.approx(be.register(str(cat.titles[col])), abs=1e-4)


def test_enrichment_is_deterministic(tmp_path):
    _make_qbias_fixture(tmp_path / "raw.csv", n=30)
    pq.prepare(str(tmp_path / "raw.csv"), str(tmp_path / "a.csv"))
    pq.prepare(str(tmp_path / "raw.csv"), str(tmp_path / "b.csv"))
    assert open(str(tmp_path / "a.csv").replace(".csv", ".register.csv")).read() == \
           open(str(tmp_path / "b.csv").replace(".csv", ".register.csv")).read()
    assert open(str(tmp_path / "a.csv").replace(".csv", ".emotion.csv")).read() == \
           open(str(tmp_path / "b.csv").replace(".csv", ".emotion.csv")).read()


def test_no_enrich_skips_sidecars(tmp_path):
    rep = pq.prepare(str(_written_fixture(tmp_path)), str(tmp_path / "clean.csv"), enrich=False)
    assert rep.enriched_articles == 0 and rep.register_path is None


def test_backend_consumes_aligned_population_enrichment(tmp_path):
    """The engine loads the sidecars, so the reference population's register/emotion ARE the
    baseline over headlines — the SAME pipeline as ingested reads (not the synthetic default)."""
    import api_server as engine
    import numpy as np
    _make_qbias_fixture(tmp_path / "raw.csv", n=140)
    rep = pq.prepare(str(tmp_path / "raw.csv"), str(tmp_path / "clean.csv"))
    profile = engine.DatasetProfile(name="qbias", kind="synthetic", domain="news",
                                    n_users=120, max_items=120, seed=0,
                                    qbias_csv=str(tmp_path / "clean.csv"),
                                    register_csv=rep.register_path, emotion_csv=rep.emotion_path)
    be = engine.Backend(profile)
    baseline = enrich.BaselineEnricher()
    reg = np.asarray(be.register, dtype=float)
    finite = np.flatnonzero(np.isfinite(reg))
    assert finite.size > 0
    for col in finite[:25]:                                   # population register == baseline
        assert reg[col] == pytest.approx(baseline.register(str(be.mind.titles[col])), abs=1e-4)


# --------------------------------------------------------------------------- #
# Enricher Quality Upgrade: when Qbias carries an article abstract (its `text` column), the
# population is enriched on headline + abstract — the SAME rich text a read's og:description gives
# — not the headline alone. This is what spreads Reporting Ratio variance across the population.
# --------------------------------------------------------------------------- #
_ABSTRACTS = [
    "Emergency crews said the data and findings confirm a deadly danger, according to a report.",   # fear
    "A statement said a survey reported furious backlash as critics condemned the scandal.",        # outrage
    "Poll results said figures revealed a celebrated historic record win, officials announced.",    # positive
    "The study finds, according to survey data, a clear breakdown of the numbers by the numbers.",  # analysis
    "The council released the annual figures in a statement, officials confirmed.",                 # reporting
    "Here's why we should act — the columnist argues we must, in defense of the plan.",             # opinion
    "A new poll and survey report shifting views, the study finds, data shows.",                    # reporting
]


def _make_qbias_fixture_with_abstracts(path, n=140):
    """Same as _make_qbias_fixture but with a `text` column (Qbias's abstract/lede) — the real
    Qbias schema, which the enricher now folds into the register/emotion text."""
    fields = ["heading", "text", "source", "bias_rating", "tags"]
    rows = [{"heading": _HEADLINES[i % len(_HEADLINES)], "text": _ABSTRACTS[i % len(_ABSTRACTS)],
             "source": _QSOURCES[i % len(_QSOURCES)], "bias_rating": _QBIAS[i % len(_QBIAS)],
             "tags": "politics"} for i in range(n)]
    _write_csv(path, rows, fields=fields)


def test_enrichment_uses_headline_plus_abstract(tmp_path):
    """The sidecar enriches combine_text(heading, abstract), not the heading alone — and the abstract
    shifts register for (almost) every row, which is exactly the variance the milestone needs."""
    _make_qbias_fixture_with_abstracts(tmp_path / "raw.csv", n=14)
    rep = pq.prepare(str(tmp_path / "raw.csv"), str(tmp_path / "clean.csv"))
    assert rep.enriched_articles == 14
    be = enrich.BaselineEnricher()
    with open(rep.register_path, newline="", encoding="utf-8") as f:
        reg = {r["news_id"]: float(r["reporting"]) for r in csv.DictReader(f)}
    with open(rep.emotion_path, newline="", encoding="utf-8") as f:
        emo = {r["news_id"]: r for r in csv.DictReader(f)}
    differs = 0
    for i in range(14):
        h = _HEADLINES[i % len(_HEADLINES)]
        combined = enrich.combine_text(h, _ABSTRACTS[i % len(_ABSTRACTS)])
        assert reg[f"Q{i}"] == pytest.approx(be.register(combined), abs=1e-4)          # heading+abstract
        assert float(emo[f"Q{i}"]["fear"]) == pytest.approx(be.emotion(combined)["fear"], abs=1e-4)
        if abs(be.register(combined) - be.register(h)) > 1e-9:
            differs += 1
    assert differs >= 12                                     # abstract changes register for ~all rows


def test_backend_loads_richer_sidecar_verbatim(tmp_path):
    """The engine loads whatever the sidecar holds (now heading+abstract), aligned by Q{i} id —
    verified against the sidecar file directly, so it stays correct regardless of the text combiner."""
    import api_server as engine
    import numpy as np
    _make_qbias_fixture_with_abstracts(tmp_path / "raw.csv", n=140)
    rep = pq.prepare(str(tmp_path / "raw.csv"), str(tmp_path / "clean.csv"))
    with open(rep.register_path, newline="", encoding="utf-8") as f:
        sidecar = {r["news_id"]: float(r["reporting"]) for r in csv.DictReader(f)}
    profile = engine.DatasetProfile(name="qbias", kind="synthetic", domain="news",
                                    n_users=120, max_items=120, seed=0,
                                    qbias_csv=str(tmp_path / "clean.csv"),
                                    register_csv=rep.register_path, emotion_csv=rep.emotion_path)
    be = engine.Backend(profile)
    ids = np.asarray(be.mind.dataset.item_ids)
    reg = np.asarray(be.register, dtype=float)
    finite = np.flatnonzero(np.isfinite(reg))
    assert finite.size > 0
    for col in finite[:25]:                                  # engine value == sidecar value at that id
        assert reg[col] == pytest.approx(sidecar[str(ids[col])], abs=1e-4)
