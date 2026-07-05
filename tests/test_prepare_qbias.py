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
