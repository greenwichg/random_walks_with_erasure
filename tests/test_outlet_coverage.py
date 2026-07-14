"""Tests for examples/outlet_coverage.py — the read-only unknown-outlet coverage diagnostic (W4).

Proves it (1) counts catalog articles whose outlet the registry doesn't know (NaN lean), (2) ranks
those unknown outlets by article frequency with an example URL, (3) reports the operational impact
(count + pct excluded from the recommendation corpus), and (4) exposes the registry lint via --lint.
Read-only and offline — it never mutates the store or the registry."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import ingest                       # noqa: E402
import store as store_mod           # noqa: E402
import outlet_coverage as oc        # noqa: E402


def _put(st, url, pub, lean, k):
    st.upsert_feed_article(
        canonical_url=ingest.canonical_url(url), url=url, publisher=pub, source_publisher=pub,
        title=f"t{k}", description="d", body=None, published_at="2026-07-01T00:00:00+00:00",
        source_feed="f", scored={"article_id": ingest.canonical_url(url), "outlet": pub,
                                 "lean": lean, "political": True, "title": f"t{k}"})


def _mixed_store():
    st = store_mod.Store("sqlite://")
    _put(st, "https://npr.org/a", "NPR", -1.0, 0)                # known
    _put(st, "https://foxnews.com/b", "Fox News", 1.5, 1)        # known
    for i in range(3):
        _put(st, f"https://blogx.example/{i}", "blogx.example", float("nan"), 10 + i)  # 3 unknown, one outlet
    _put(st, "https://blogy.example/z", "blogy.example", float("nan"), 20)             # 1 unknown, another
    return st


def test_scan_ranks_unknown_outlets_by_frequency_and_reports_impact():
    s = oc.scan(_mixed_store())
    assert s["total"] == 6 and s["known"] == 2 and s["unknown"] == 4
    assert s["unknownPct"] == round(100 * 4 / 6, 2)              # operational impact: % excluded
    # ranked by article volume, descending — the highest-value registry additions first
    assert [o["outlet"] for o in s["outlets"]] == ["blogx.example", "blogy.example"]
    assert s["outlets"][0]["count"] == 3 and s["outlets"][0]["example"].startswith("https://blogx")
    assert s["registryOutlets"] > 0                             # context: how many outlets are known


def test_scan_reports_full_coverage_when_all_outlets_known():
    st = store_mod.Store("sqlite://")
    _put(st, "https://npr.org/a", "NPR", -1.0, 0)
    _put(st, "https://foxnews.com/b", "Fox News", 1.5, 1)
    s = oc.scan(st)
    assert s["unknown"] == 0 and s["outlets"] == [] and s["unknownPct"] == 0.0


def test_render_is_readable_and_names_the_next_action():
    out = oc._render(oc.scan(_mixed_store()), top=20)
    assert "unknown outlet" in out and "blogx.example" in out
    assert "excluded from the recommendation corpus" in out and "outlet_registry.csv" in out


def test_main_lint_mode_passes_on_the_bundled_registry(capsys):
    assert oc.main(["--lint"]) == 0                             # clean registry -> exit 0
    assert "OK" in capsys.readouterr().out


def test_scan_is_read_only(tmp_path):
    """A scan must not change the catalog (count identical before/after)."""
    dburl = f"sqlite:///{tmp_path/'cov.db'}"
    st = store_mod.Store(dburl)
    _put(st, "https://blogx.example/1", "blogx.example", float("nan"), 1)
    before = st.count_feed_articles()
    oc.scan(store_mod.Store(dburl))
    assert store_mod.Store(dburl).count_feed_articles() == before
