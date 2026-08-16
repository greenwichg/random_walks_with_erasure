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


def _row(url, v1locations, collection="1", image=""):
    """One GKG CSV row with the columns the parser reads populated (27-col layout)."""
    cols = [""] * 27
    cols[gdelt_gkg._COL_COLLECTION] = collection
    cols[gdelt_gkg._COL_DOCUMENT] = url
    cols[gdelt_gkg._COL_V1LOCATIONS] = v1locations
    cols[gdelt_gkg._COL_SHARING_IMAGE] = image
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


def test_window_urls_walks_back_fifteen_minute_files():
    latest = "http://data.gdeltproject.org/gdeltv2/20260726001500.gkg.csv.zip"
    urls = gdelt_gkg.window_urls(latest, 3)
    assert urls == [
        "http://data.gdeltproject.org/gdeltv2/20260726001500.gkg.csv.zip",
        "http://data.gdeltproject.org/gdeltv2/20260726000000.gkg.csv.zip",
        "http://data.gdeltproject.org/gdeltv2/20260725234500.gkg.csv.zip",  # crosses the day line
    ]
    assert gdelt_gkg.window_urls(latest, 1) == [latest]
    assert gdelt_gkg.window_urls("http://x/odd-name.zip", 5) == ["http://x/odd-name.zip"]


def test_enrich_lookback_covers_earlier_windows_and_survives_gaps():
    """The reason the lookback exists: catalog articles were processed by GDELT in EARLIER
    windows, so the latest file alone would ~never match. Older windows contribute their
    records, the newest window wins a duplicate URL, and a missing window (GDELT gap) is
    counted, not fatal."""
    st = store_mod.Store("sqlite://")
    _upsert(st, "https://old.example/story")
    _upsert(st, "https://dup.example/story")
    base = "http://data.gdeltproject.org/gdeltv2/"
    latest, older = f"{base}20260726001500.gkg.csv.zip", f"{base}20260726000000.gkg.csv.zip"
    payloads = {
        gdelt_gkg.LASTUPDATE_URL: f"1 a {latest}".encode(),
        # newest window: only the duplicate URL, located FR
        latest: _zip_bytes(_row("https://dup.example/story", "1#France#FR##48#2#FR")),
        # older window: the catalog article + the duplicate URL with a DIFFERENT country
        older: _zip_bytes("\n".join([
            _row("https://old.example/story", "1#Japan#JA##36#138#JA"),
            _row("https://dup.example/story", "1#Germany#GM##51#9#GM"),
        ])),
        # the third window is absent → KeyError → counted as a gap, cycle continues
    }
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: payloads[u], windows=3)
    assert stats == {"windows": 2, "windowErrors": 1, "records": 3, "matched": 2, "located": 2, "images": 0}
    assert st.event_countries_for_urls(["https://old.example/story"]) == {
        "https://old.example/story": ["JP"]}                         # earlier window matched
    assert st.event_countries_for_urls(["https://dup.example/story"]) == {
        "https://dup.example/story": ["FR"]}                         # newest window won the dup


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
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: payloads[u], windows=1)
    assert stats == {"windows": 1, "windowErrors": 0, "records": 3, "matched": 2, "located": 2, "images": 0}
    assert st.event_countries_for_urls(["https://known.example/story"]) == {
        "https://known.example/story": ["JP"]}                       # FIPS JA → ISO JP, by name
    assert st.event_countries_for_urls(["http://schemeflip.example/a"]) == {
        "http://schemeflip.example/a": ["DE"]}
    # Re-running the same cycle is harmless (per-source replace, same result).
    assert gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: payloads[u], windows=1)["located"] == 2


def test_enrich_honours_size_cap_and_empty_manifest():
    st = store_mod.Store("sqlite://")
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: b"no gkg line")
    assert stats["located"] == 0 and "no gkg" in stats["skipped"]
    gkg_url = "http://data.gdeltproject.org/gdeltv2/x.gkg.csv.zip"
    payloads = {gdelt_gkg.LASTUPDATE_URL: f"1 a {gkg_url}".encode(), gkg_url: b"x" * 100}
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: payloads[u], max_bytes=10, windows=1)
    assert stats == {"windows": 0, "windowErrors": 1, "records": 0, "matched": 0, "located": 0, "images": 0}


def test_cold_start_auto_backfill(monkeypatch):
    """No manual backfill dance: an EMPTY event table over a NON-EMPTY catalog makes the first
    cycle deep automatically (default 96 windows, flagged `backfill` in the stats); once located
    rows exist — or when the catalog is empty — cycles use the steady-state lookback."""
    monkeypatch.delenv("RWE_GDELT_GKG_WINDOWS", raising=False)
    monkeypatch.delenv("RWE_GDELT_GKG_BACKFILL_WINDOWS", raising=False)
    base = "http://data.gdeltproject.org/gdeltv2/"
    latest = f"{base}20260726001500.gkg.csv.zip"
    calls = []

    def fetch(url):
        calls.append(url)
        if url == gdelt_gkg.LASTUPDATE_URL:
            return f"1 a {latest}".encode()
        if url == latest:
            return _zip_bytes(_row("https://known.example/story", "1#Japan#JA##36#138#JA"))
        raise KeyError(url)                     # older windows absent → counted, not fatal

    # Non-empty catalog + empty event table → deep first cycle (96 windows attempted).
    st = store_mod.Store("sqlite://")
    _upsert(st, "https://known.example/story")
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=fetch)
    assert stats["backfill"] is True and stats["located"] == 1
    assert stats["windows"] + stats["windowErrors"] == 96
    # LUKEWARM still backfills (the production lesson): a handful of rows trickled in by
    # steady-state cycles before any deep pass must not read as "warm". 1 row < threshold 25.
    assert st.count_event_locations() == 1
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=fetch)
    assert stats["backfill"] is True
    # Genuinely warm (≥ threshold rows) → steady-state depth, no backfill flag.
    for i in range(30):
        _upsert(st, f"https://warm.example/{i}")
        st.replace_article_event_locations(
            f"https://warm.example/{i}",
            __import__("location").resolve_event_locations([{"country": "US", "source": "gdelt-gkg"}]))
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=fetch)
    assert "backfill" not in stats and stats["windows"] + stats["windowErrors"] == 4
    # The adapter allows the deep pass at most ONCE per process: a catalog that never crosses
    # the threshold (low GDELT overlap) must not re-download 24 h of GKG every 15 minutes.
    monkeypatch.setenv("RWE_GDELT_GKG", "1")
    st4 = store_mod.Store("sqlite://")
    _upsert(st4, "https://known.example/story")
    adapter = sources.GDELTGKGEnricher(fetch_bytes=fetch)
    first = adapter.poll_once(st4, scorer=None)
    second = adapter.poll_once(st4, scorer=None)
    assert first["windows"] + first["windowErrors"] == 96
    assert second["windows"] + second["windowErrors"] == 4
    # Empty catalog → never deep (nothing to locate on a fresh deployment).
    st2 = store_mod.Store("sqlite://")
    stats = gdelt_gkg.enrich_from_latest(st2, fetch_bytes=fetch)
    assert "backfill" not in stats and stats["windows"] + stats["windowErrors"] == 4
    # Explicit windows= always wins; RWE_GDELT_GKG_BACKFILL_WINDOWS=0 disables auto-backfill.
    st3 = store_mod.Store("sqlite://")
    _upsert(st3, "https://known.example/story")
    assert "backfill" not in gdelt_gkg.enrich_from_latest(st3, fetch_bytes=fetch, windows=1)
    monkeypatch.setenv("RWE_GDELT_GKG_BACKFILL_WINDOWS", "0")
    stats = gdelt_gkg.enrich_from_latest(st3, fetch_bytes=fetch)
    assert "backfill" not in stats and stats["windows"] + stats["windowErrors"] == 4


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


def test_sharing_image_backfills_missing_thumbnails_only():
    """V2.1SHARINGIMAGE (col 18) supplies thumbnails for articles whose feed shipped no media —
    backfill-when-empty ONLY: a feed-provided image is never overwritten, records with an image
    but no resolvable place still count, and non-URLs are dropped."""
    st = store_mod.Store("sqlite://")
    _upsert(st, "https://bare.example/story")                        # no image → backfill target
    _upsert(st, "https://has.example/story", image="https://feed.example/own.jpg")
    csv_text = "\n".join([
        # image + location: both enrichments land
        _row("https://bare.example/story", "1#Japan#JA##36#138#JA",
             image="https://cdn.example/hero.jpg"),
        # image but NO resolvable place: record must still be kept and backfill applied
        _row("https://has.example/story", "", image="https://cdn.example/other.jpg"),
        # junk image value: dropped at parse
        _row("https://junk.example/story", "", image="not-a-url"),
    ])
    parsed = gdelt_gkg.parse_gkg_csv(csv_text)
    assert [(u, i) for u, _p, i in parsed] == [
        ("https://bare.example/story", "https://cdn.example/hero.jpg"),
        ("https://has.example/story", "https://cdn.example/other.jpg"),
    ]
    gkg_url = "http://data.gdeltproject.org/gdeltv2/x.gkg.csv.zip"
    payloads = {gdelt_gkg.LASTUPDATE_URL: f"1 a {gkg_url}".encode(), gkg_url: _zip_bytes(csv_text)}
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: payloads[u], windows=1)
    assert stats["images"] == 1 and stats["located"] == 1            # only the bare row gained one
    rows = {r["canonicalUrl"]: r for r in st.search_feed_articles()[0]}
    assert rows["https://bare.example/story"]["image"] == "https://cdn.example/hero.jpg"
    assert rows["https://bare.example/story"]["imageSource"] == "gdelt-gkg"   # provenance kept
    assert rows["https://has.example/story"]["image"] == "https://feed.example/own.jpg"  # untouched
    # Idempotence + guards: a filled row is never re-written; unknown rows are a no-op.
    assert st.backfill_article_image("https://bare.example/story", "https://cdn.example/again.jpg") is False
    assert st.backfill_article_image("https://has.example/story", "https://cdn.example/x.jpg") is False
    assert st.backfill_article_image("https://missing.example/x", "https://cdn.example/x.jpg") is False


# --------------------------------------------------------------------------- #
# X5 rung 2 — entities from the SAME GKG file (docs/STORY_ENTITY_EVIDENCE_PLAN.md).
# Contract under test: the location/image path stays byte-identical (its record shape is pinned
# above), entity persistence is opt-in and off by default, the backfill CLI writes ONLY the
# entities table, and normalization happens once at parse so identity needs no re-normalizing.
# --------------------------------------------------------------------------- #
import gdelt_entity_backfill     # noqa: E402


def _erow(url, persons="", orgs="", collection="1"):
    cols = [""] * 27
    cols[gdelt_gkg._COL_COLLECTION] = collection
    cols[gdelt_gkg._COL_DOCUMENT] = url
    cols[gdelt_gkg._COL_V1PERSONS] = persons
    cols[gdelt_gkg._COL_V1ORGS] = orgs
    return "\t".join(cols)


def test_split_entities_normalizes_dedupes_and_caps():
    assert gdelt_gkg._split_entities("Barack Obama;  JOHN  Kerry ;barack obama;xi") == \
        ["barack obama", "john kerry"]           # normalized, deduped, len>=3 floor drops "xi"
    assert gdelt_gkg._split_entities("") == []
    many = ";".join(f"person {i}" for i in range(50))
    assert len(gdelt_gkg._split_entities(many)) == gdelt_gkg.ENTITY_CAP
    # Dedup BEFORE the cap: a repetitive record must not get a smaller signal.
    repeats = ";".join(["Same Name"] * 40 + [f"other {i}" for i in range(30)])
    assert len(gdelt_gkg._split_entities(repeats)) == gdelt_gkg.ENTITY_CAP


def test_parse_gkg_entity_lines_applies_the_web_discipline():
    text = "\n".join([
        _erow("https://a.example/x", persons="Jane Doe;Ali Khan", orgs="Acme Corp"),
        _erow("https://cite.example/x", persons="Jane Doe", collection="2"),   # citation: skip
        _erow("notaurl", persons="Jane Doe"),                                  # no URL: skip
        _erow("https://none.example/x"),                                       # no names: skip
        "short\trow",
    ])
    parsed = gdelt_gkg.parse_gkg_entity_lines(text.splitlines())
    assert len(parsed) == 1
    url, ents = parsed[0]
    assert url == "https://a.example/x"
    assert ents == {"person": ["jane doe", "ali khan"], "org": ["acme corp"]}


def test_entity_persistence_is_opt_in_and_off_by_default(monkeypatch):
    st = store_mod.Store("sqlite://")
    _upsert(st, "https://a.example/x")
    text = _erow("https://a.example/x", persons="Jane Doe") + "\n" + \
        _row("https://a.example/x", "1#France#FR##48#2#FR")
    gkg_url = "http://data.gdeltproject.org/gdeltv2/x.gkg.csv.zip"
    payloads = {gdelt_gkg.LASTUPDATE_URL: f"1 a {gkg_url}".encode(), gkg_url: _zip_bytes(text)}

    monkeypatch.delenv("RWE_GDELT_ENTITIES", raising=False)
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: payloads[u], windows=1)
    assert "entities" not in stats and st.count_article_entities() == 0, \
        "the default cycle must not even report the counter — off means off"

    monkeypatch.setenv("RWE_GDELT_ENTITIES", "1")
    stats = gdelt_gkg.enrich_from_latest(st, fetch_bytes=lambda u: payloads[u], windows=1)
    assert stats["entities"] == 1 and stats["located"] >= 0
    assert st.entities_for_urls(["https://a.example/x"]) == \
        {"https://a.example/x": {"person": ["jane doe"]}}


def test_replace_article_entities_is_per_source_idempotent():
    st = store_mod.Store("sqlite://")
    assert st.replace_article_entities("https://a.example/x",
                                       {"person": ["jane doe"], "org": ["acme corp"]}) == 2
    assert st.replace_article_entities("https://a.example/x", {"person": ["ali khan"]}) == 1
    got = st.entities_for_urls(["https://a.example/x"])["https://a.example/x"]
    assert got == {"person": ["ali khan"]}, "same source replaces, never accumulates"
    st.replace_article_entities("https://a.example/x", {"org": ["other org"]}, source="manual")
    got = st.entities_for_urls(["https://a.example/x"])["https://a.example/x"]
    assert got == {"person": ["ali khan"], "org": ["other org"]}, \
        "a different source replaces only ITS OWN rows — gdelt-gkg's survive alongside"
    assert st.replace_article_entities("https://a.example/x", {}) == 0
    got = st.entities_for_urls(["https://a.example/x"])["https://a.example/x"]
    assert got == {"person": ["ali khan"], "org": ["other org"]}, \
        "empty input is a no-op, never a delete — a nameless record must not erase an earlier one"


def test_backfill_writes_entities_and_nothing_else(monkeypatch):
    """The production-data neutrality pin: locations and images stay byte-identical however rich
    the GKG windows are."""
    st = store_mod.Store("sqlite://")
    _upsert(st, "https://a.example/x")
    _upsert(st, "https://b.example/x")
    text = "\n".join([
        _erow("https://a.example/x", persons="Jane Doe", orgs="Acme Corp"),
        _row("https://a.example/x", "1#France#FR##48#2#FR", image="https://cdn.example/i.jpg"),
        _erow("https://unknown.example/x", persons="Nobody Here"),   # not in catalog: no write
    ])
    gkg_url = "http://data.gdeltproject.org/gdeltv2/20260726120000.gkg.csv.zip"
    payloads = {gdelt_gkg.LASTUPDATE_URL: f"1 a {gkg_url}".encode(), gkg_url: _zip_bytes(text)}

    stats = gdelt_entity_backfill.backfill(st, fetch_bytes=lambda u: payloads.get(u, b""),
                                           windows=1)
    assert stats["articlesWritten"] == 1 and stats["rowsWritten"] == 2
    assert st.count_article_entities() == 2
    assert st.count_event_locations() == 0, "the backfill must NEVER write locations"
    rows = {r["canonicalUrl"]: r for r in st.search_feed_articles()[0]}
    assert rows["https://a.example/x"]["image"] is None, "images untouched by design"


def test_backfill_counts_gaps_instead_of_dying(monkeypatch):
    st = store_mod.Store("sqlite://")
    _upsert(st, "https://a.example/x")
    good = "http://data.gdeltproject.org/gdeltv2/20260726120000.gkg.csv.zip"
    text = _erow("https://a.example/x", persons="Jane Doe")

    def fetch(url):
        if url == gdelt_gkg.LASTUPDATE_URL:
            return f"1 a {good}".encode()
        if url == good:
            return _zip_bytes(text)
        raise OSError("gap")                     # every older window is missing

    stats = gdelt_entity_backfill.backfill(st, fetch_bytes=fetch, windows=3)
    assert stats["windows"] == 1 and stats["windowErrors"] == 2
    assert stats["articlesWritten"] == 1
