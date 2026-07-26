"""Tests for examples/gdelt_gkg.py — the GDELT GKG event-geography enricher (Phase 2 supply).

Offline by design (stub fetchers + in-memory zips), like every other adapter test: proves the
FIPS trap is bypassed (names, never codes), dominant-country salience, catalog matching through
the shared canonicalizer (scheme-flip included), per-source idempotent writes, and the poller
adapter contract (default OFF, health aggregate shape)."""

import io
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import gdelt_gkg                 # noqa: E402
import sources                   # noqa: E402
import store as store_mod        # noqa: E402


def _row(url, v1locations, collection="1"):
    """One GKG CSV row with the columns the parser reads populated (27-col layout)."""
    cols = [""] * 27
    cols[gdelt_gkg._COL_COLLECTION] = collection
    cols[gdelt_gkg._COL_DOCUMENT] = url
    cols[gdelt_gkg._COL_V1LOCATIONS] = v1locations
    return "\t".join(cols)


def _zip_bytes(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("20260726120000.gkg.csv", text)
    return buf.getvalue()


def _upsert(st, canon, **over):
    kw = dict(canonical_url=canon, url=canon, publisher=over.pop("publisher", "BBC"),
              source_publisher=None, title="t", description="", body=None,
              published_at="2026-07-25T10:00:00Z", source_feed="feed",
              scored={"lean": 0.0, "category": "World"})
    kw.update(over)
    return st.upsert_feed_article(**kw)


def test_parse_lastupdate_picks_the_gkg_file():
    manifest = ("123 abc http://data.gdeltproject.org/gdeltv2/x.export.CSV.zip\n"
                "456 def http://data.gdeltproject.org/gdeltv2/x.mentions.CSV.zip\n"
                "789 ghi http://data.gdeltproject.org/gdeltv2/20260726120000.gkg.csv.zip\n")
    assert gdelt_gkg.parse_lastupdate(manifest).endswith("20260726120000.gkg.csv.zip")
    assert gdelt_gkg.parse_lastupdate("no gkg here") is None
    assert gdelt_gkg.parse_lastupdate("") is None


def test_dominant_places_dodges_the_fips_trap():
    """FIPS 'AS' is Australia (ISO 'AS' = American Samoa) and FIPS 'GM' is Germany (ISO 'GM' =
    Gambia): the parser must resolve by NAME, never by the code column."""
    blocks = "1#Australia#AS##-25#135#AS;1#Germany#GM##51#9#GM"
    places = gdelt_gkg._dominant_places(blocks)
    assert sorted(p["country"] for p in places) == ["AU", "DE"]      # a tie: both kept
    assert all(p["source"] == "gdelt-gkg" for p in places)


def test_dominant_places_salience_and_fail_honest():
    # 3 US blocks (city/state/country forms) vs 1 stray Australia mention → US only.
    blocks = ("2#San Diego, California, United States#US#USCA#32.7#-117.1#123;"
              "3#California, United States#US#USCA#36.7#-119.4#456;"
              "1#United States#US##39.7#-98.5#US;"
              "1#Australia#AS##-25#135#AS")
    places = gdelt_gkg._dominant_places(blocks)
    assert [p["country"] for p in places] == ["US"]
    assert places[0]["lat"] == 32.7                                  # first block's coordinates
    # Unknown names are dropped, never guessed; empty/malformed fields never raise.
    assert gdelt_gkg._dominant_places("1#Atlantis#AA##0#0#XX") == []
    assert gdelt_gkg._dominant_places("") == []
    assert gdelt_gkg._dominant_places("garbage#too#short") == []


def test_parse_gkg_csv_skips_non_web_and_malformed_rows():
    text = "\n".join([
        _row("https://example.com/a", "1#France#FR##48#2#FR"),
        _row("https://example.com/cite", "1#France#FR##48#2#FR", collection="2"),  # citation: skip
        _row("notaurl", "1#France#FR##48#2#FR"),                                   # no URL: skip
        "short\trow",                                                              # malformed: skip
        _row("https://example.com/none", ""),                                      # no locations: skip
    ])
    parsed = gdelt_gkg.parse_gkg_csv(text)
    assert len(parsed) == 1 and parsed[0][0] == "https://example.com/a"
    assert parsed[0][1][0]["country"] == "FR"


def test_enrich_from_latest_matches_catalog_and_persists(monkeypatch):
    st = store_mod.Store("sqlite://")
    _upsert(st, "https://known.example/story")                       # in catalog (https canonical)
    _upsert(st, "http://schemeflip.example/a")                       # catalog kept http
    csv_text = "\n".join([
        # matches the catalog exactly (tracking params stripped by the shared canonicalizer)
        _row("https://known.example/story?utm_source=x", "1#Japan#JA##36#138#JA"),
        # GKG recorded https where the catalog holds http → the scheme-flip candidate matches
        _row("https://schemeflip.example/a", "1#Germany#GM##51#9#GM"),
        # not in the catalog at all → never written
        _row("https://unknown.example/z", "1#France#FR##48#2#FR"),
    ])
    gkg_url = "http://data.gdeltproject.org/gdeltv2/20260726120000.gkg.csv.zip"
    payloads = {gdelt_gkg.LASTUPDATE_URL: f"1 a {gkg_url}".encode(),
                gkg_url: _zip_bytes(csv_text)}
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: payloads[u])
    assert stats == {"records": 3, "matched": 2, "located": 2}
    assert st.event_countries_for_urls(["https://known.example/story"]) == {
        "https://known.example/story": ["JP"]}                       # FIPS JA → ISO JP, by name
    assert st.event_countries_for_urls(["http://schemeflip.example/a"]) == {
        "http://schemeflip.example/a": ["DE"]}
    # Re-running the same cycle is harmless (per-source replace, same result).
    assert gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: payloads[u])["located"] == 2


def test_enrich_honours_size_cap_and_empty_manifest():
    st = store_mod.Store("sqlite://")
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: b"no gkg line")
    assert stats["located"] == 0 and "no gkg" in stats["skipped"]
    gkg_url = "http://data.gdeltproject.org/gdeltv2/x.gkg.csv.zip"
    payloads = {gdelt_gkg.LASTUPDATE_URL: f"1 a {gkg_url}".encode(), gkg_url: b"x" * 100}
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: payloads[u], max_bytes=10)
    assert stats["located"] == 0 and "exceeds cap" in stats["skipped"]


def test_enricher_adapter_contract(monkeypatch):
    monkeypatch.delenv("RWE_GDELT_GKG", raising=False)
    a = sources.GDELTGKGEnricher()
    assert a.enabled() is False                                      # default OFF — safe deploy
    monkeypatch.setenv("RWE_GDELT_GKG", "1")
    assert a.enabled() is True and a.health_key == "gdelt://gkg"

    st = store_mod.Store("sqlite://")
    _upsert(st, "https://known.example/story")
    gkg_url = "http://data.gdeltproject.org/gdeltv2/x.gkg.csv.zip"
    payloads = {gdelt_gkg.LASTUPDATE_URL: f"1 a {gkg_url}".encode(),
                gkg_url: _zip_bytes(_row("https://known.example/story", "1#Japan#JA##36#138#JA"))}
    ok = sources.GDELTGKGEnricher(fetch_bytes=lambda u: payloads[u]).poll_once(st, scorer=None)
    assert ok["ok"] == 1 and ok["failed"] == 0 and ok["located"] == 1
    assert ok["provider"] == "GDELT-GKG" and ok["sourceType"] == "gdelt-gkg"

    def boom(_u):
        raise OSError("network down")
    bad = sources.GDELTGKGEnricher(fetch_bytes=boom).poll_once(st, scorer=None)
    assert bad["ok"] == 0 and bad["failed"] == 1 and bad["errors"][0]["feed"] == "gdelt://gkg"
    assert bad["located"] == 0                                       # an outage never fabricates
