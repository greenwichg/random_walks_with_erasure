"""Tests for examples/resolve_msn_publisher.py.

The live fetch needs open network (Colab), so it is NOT exercised here; instead the
five-strategy HTML parser, the priority order, and resolve()/coverage are tested against
synthetic MSN-shaped snapshots and an injected fetch function."""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "resolve_msn_publisher", ROOT / "examples" / "resolve_msn_publisher.py")
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)

LEAN_CSV = str(ROOT / "examples" / "data" / "outlet_lean.csv")


def test_parse_jsonld_publisher():
    html = ('<html><head><script type="application/ld+json">'
            '{"@type":"NewsArticle","publisher":{"@type":"Organization","name":"CNN"}}'
            '</script></head></html>')
    assert rp.parse_publisher(html) == ("CNN", "jsonld")


def test_parse_og_site_name_and_msn_suffix_strip():
    html = '<meta property="og:site_name" content="Fox News - MSN">'
    assert rp.parse_publisher(html) == ("Fox News", "site_name")


def test_parse_provider_json_blob():
    html = '<script>window.__data={"providerName":"Reuters","x":1};</script>'
    assert rp.parse_publisher(html) == ("Reuters", "provider_json")


def test_parse_canonical_host():
    html = '<link rel="canonical" href="https://www.washingtonpost.com/politics/a.html">'
    assert rp.parse_publisher(html) == ("washingtonpost.com", "canonical")


def test_parse_byline_fallback():
    html = "<article><p>Provided by The Associated Press</p></article>"
    pub, strat = rp.parse_publisher(html)
    assert pub == "The Associated Press" and strat == "byline"


def test_priority_jsonld_beats_site_name():
    html = ('<meta property="og:site_name" content="MSN">'
            '<script type="application/ld+json">{"publisher":{"name":"NPR"}}</script>')
    assert rp.parse_publisher(html) == ("NPR", "jsonld")


def test_no_attribution_returns_none():
    assert rp.parse_publisher("<html><body>just text</body></html>") == (None, None)
    assert rp.parse_publisher("") == (None, None)


def test_clean_unescapes_and_trims():
    assert rp._clean("  Fox&amp;Friends  ") == "Fox&Friends"
    assert rp._clean("CNN &mdash; MSN") == "CNN"        # trailing dash-MSN stripped


def test_resolve_with_injected_fetch():
    snaps = {
        "A": '<meta property="og:site_name" content="CNN">',
        "B": '<script type="application/ld+json">{"publisher":{"name":"Reuters"}}</script>',
        "C": None,                                       # fetch failed
        "D": "<html>no attribution</html>",              # fetched, unparseable
    }
    resolved, counts, n_fetched = rp.resolve(
        ["A", "B", "C", "D"], fetch_fn=lambda nid: snaps[nid], log_every=0)
    assert resolved == {"A": "CNN", "B": "Reuters"}
    assert n_fetched == 3                                # A, B, D fetched; C failed
    assert counts["site_name"] == 1 and counts["jsonld"] == 1


def test_lean_coverage_joins_names_and_domains():
    from rwe.mind import load_lean_table
    table = load_lean_table(LEAN_CSV)
    resolved = {"N1": "CNN", "N2": "Fox News",
                "N3": "washingtonpost.com",             # domain joins via _norm -> "Washington Post"
                "N4": "Some Local Blog"}                 # not in the table
    art, uniq, unmatched = rp.lean_coverage(resolved, table)
    assert art == 3 and uniq == 3
    assert list(unmatched) == ["Some Local Blog"]


def test_write_source_map_format(tmp_path):
    out = tmp_path / "sm.tsv"
    n = rp.write_source_map({"N1": "CNN", "N2": "Fox News"}, str(out))
    assert n == 2
    lines = out.read_text().splitlines()
    assert "N1\tCNN" in lines and "N2\tFox News" in lines


def test_political_news_ids_on_fixture():
    fix = str(ROOT / "tests" / "fixtures" / "mind_demo")
    all_ids = rp._political_news_ids(fix, political_only=False)
    pol_ids = rp._political_news_ids(fix, political_only=True)
    assert len(all_ids) == 8                             # the fixture has 8 articles
    assert 0 < len(pol_ids) <= len(all_ids) and all(isinstance(i, str) for i in pol_ids)


def test_fetch_returns_none_on_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("blocked")
    monkeypatch.setattr(rp.urllib.request, "urlopen", boom)
    assert rp.fetch_snapshot("N1", retries=1) is None    # graceful, no exception
