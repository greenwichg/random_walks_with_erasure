"""Commit R1 — article-level political classification behind the cross-cutting gate.

Proves the full truth layer:
  * ``ingest.looks_political`` — the ONE shared heuristic (URL path hints + category hints);
  * the qbias CSV carries the scored ``political`` flag and ``catalog_from_qbias`` consumes it
    (explicit column first, derivation fallback, never all-ones);
  * ``_cross_of`` requires a political article — a promo/sports piece from a leaning outlet can
    no longer be cross-cutting on house lean alone;
  * the rwe-b slice admits political items only (``_slice_admits``), other strategies everything;
  * the Evidence Resolver never explains a non-political article as "another political
    perspective", and ``validate()`` flags such an explanation as unsupported.
"""

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import api_server            # noqa: E402
import evidence_resolver as er  # noqa: E402
import feed_source           # noqa: E402
from ingest import looks_political  # noqa: E402
from simulate_users import catalog_from_qbias  # noqa: E402


# --------------------------------------------------------------------------- heuristic
def test_looks_political_url_and_category_hints():
    assert looks_political(url="https://ex.com/politics/story")
    assert looks_political(url="https://ex.com/2026/election-night")
    assert looks_political(url="https://ex.com/opinion/columnist")     # "/opinion" path hint
    assert looks_political(url="https://ex.com/a", category="Politics")
    assert looks_political(url="", category="Opinion")
    assert not looks_political(url="https://ex.com/sports/final", category="Sports")
    assert not looks_political(url="https://ex.com/betting/promo-code", category="")
    assert not looks_political()


# --------------------------------------------------------------------------- CSV -> catalog
def _row(url, publisher, lean, category, political, title="Story about the vote"):
    return {"title": title, "publisher": publisher, "url": url,
            "scored": {"outlet": publisher, "category": category, "lean": lean,
                       "political": political, "title": title}}


def test_csv_carries_political_and_loader_consumes_it(tmp_path):
    rows = [
        _row("https://ex.com/right/pol", "Fox News", 1.6, "Politics", True),
        _row("https://ex.com/right/promo", "Fox News", 1.6, "Shopping", False),
        _row("https://ex.com/left/pol", "The Guardian", -1.5, "Politics", True),
        _row("https://ex.com/left/sport", "The Guardian", -1.5, "Sports", False),
    ]
    path = str(tmp_path / "cand.csv")
    feed_source.export_candidate_csv(rows, path)
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    assert "political" in header

    cat = catalog_from_qbias(path)
    assert cat.political is not None
    by_title = dict(zip(cat.titles.tolist(), cat.political.tolist()))
    # row order == title order here (unique titles per row)
    got = {u: bool(p) for u, p in zip([r["url"] for r in rows], cat.political.tolist())}
    assert got["https://ex.com/right/pol"] is True
    assert got["https://ex.com/right/promo"] is False
    assert got["https://ex.com/left/pol"] is True
    assert got["https://ex.com/left/sport"] is False
    assert not cat.political.all(), "the mask must be real, never assumed all-political"


def test_loader_derives_when_column_absent(tmp_path):
    # a legacy 5-column CSV (no political): derive from tags + url, never all-ones
    import csv as _csv
    path = str(tmp_path / "legacy.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["title", "source", "bias_rating", "tags", "url"])
        w.writerow(["Vote splits senate", "Fox News", "right", "['Politics']", "https://ex.com/a"])
        w.writerow(["Cup final recap", "Fox News", "right", "['Sports']", "https://ex.com/b"])
        w.writerow(["Column on the race", "AP", "center", "", "https://ex.com/opinion/c"])
    cat = catalog_from_qbias(path)
    flags = dict(zip(cat.titles.tolist(), cat.political.tolist()))
    assert flags["Vote splits senate"] is np.True_ or flags["Vote splits senate"] is True
    assert not flags["Cup final recap"]
    assert flags["Column on the race"]          # "/opinion" URL hint


# --------------------------------------------------------------------------- the gate
def test_cross_of_requires_political():
    left_reader = -1.0
    assert api_server._cross_of(left_reader, 1.6, True)          # political right article: crosses
    assert not api_server._cross_of(left_reader, 1.6, False)     # promo with house lean: never
    assert not api_server._cross_of(left_reader, 0.2, True)      # centre: |lean| < 0.5
    assert not api_server._cross_of(0.0, 1.6, True)              # sideless reader: never


def test_slice_select_orders_cross_first_with_same_side_fallback():
    """Commit R1.5: the rwe-b slice serves opposing-viewpoint items first, same-side political
    items only as fallback; rank order is preserved within each tier and for every other case."""
    class _Mind:
        # cols:            0     1     2     3     4
        item_positions = np.array([1.6, -1.4, 1.2, -0.9, 0.2])
    left_reader = -1.0
    admitted = [1, 0, 3, 2, 4]          # rank order (all political by admission)
    # cross for a left reader = right items (0, 2); same-side/centre (1, 3, 4) fall back, rank order
    assert api_server.Backend._slice_select(_Mind, "rwe-b", admitted, 5, left_reader) == [0, 2, 1, 3, 4]
    # enough cross candidates -> the slice is cross-only
    assert api_server.Backend._slice_select(_Mind, "rwe-b", admitted, 2, left_reader) == [0, 2]
    # fewer cross than k -> same-side fallback fills the remainder
    assert api_server.Backend._slice_select(_Mind, "rwe-b", admitted, 3, left_reader) == [0, 2, 1]
    # sideless reader: no cross direction -> pure rank order
    assert api_server.Backend._slice_select(_Mind, "rwe-b", admitted, 3, 0.0) == [1, 0, 3]
    # other strategies: pure rank order regardless of side
    assert api_server.Backend._slice_select(_Mind, "rwe-d", admitted, 3, left_reader) == [1, 0, 3]


def test_slice_admission_is_rwe_b_only():
    class _Mind:
        political = np.array([True, False, True])
    assert api_server.Backend._slice_admits(_Mind, "rwe-b", 0)
    assert not api_server.Backend._slice_admits(_Mind, "rwe-b", 1)
    assert api_server.Backend._slice_admits(_Mind, "rwe-d", 1)    # discovery admits everything
    assert api_server.Backend._slice_admits(_Mind, "adaptive", 1)

    class _NoMask:
        political = None
    assert api_server.Backend._slice_admits(_NoMask, "rwe-b", 1)  # unknown mask: admit (legacy)


# --------------------------------------------------------------------------- the resolver
def _rec(political, cross=True):
    art = {"url": "https://ex.com/x", "id": "https://ex.com/x", "publisher": "Fox News",
           "topic": "Politics", "lean": 1.6, "publishedAt": "2026-07-09T09:00:00+00:00"}
    if political is not None:
        art["political"] = political
    return {"article": art, "crossCutting": cross, "strategy": "rwe-b"}


def test_resolver_never_bridges_a_non_political_article():
    out = er.resolve(_rec(political=False), {}, {})
    assert out["type"] != "bridge"
    assert out["type"] == "coverage_breadth"      # empty ctx: falls through to the claim-free P6


def test_resolver_bridges_political_and_legacy_payloads():
    assert er.resolve(_rec(political=True), {}, {})["type"] == "bridge"
    assert er.resolve(_rec(political=None), {}, {})["type"] == "bridge"   # legacy: no flag, no block


def test_validate_flags_bridge_on_non_political_article():
    rec = _rec(political=True)
    exp = er.resolve(rec, {}, {})
    assert exp["type"] == "bridge" and er.validate(exp, rec, {}, {}) == []
    # the same explanation shown for a non-political article is an over-claim
    bad = _rec(political=False)
    fails = er.validate(exp, bad, {}, {})
    assert any("non-political" in f for f in fails)
