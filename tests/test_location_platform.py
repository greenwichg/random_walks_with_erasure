"""Location Intelligence Platform (Phase 0 + 1) — resolver, persistence, search, registry
locality, settings contract, Local News v1 filtering, and Geographic Diversity readiness.

Offline by construction: the GDELT leg uses the adapter's injectable ``fetch``; everything else
runs against a temp SQLite store and an in-repo registry file.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples"))

import location                      # noqa: E402
import outlet_registry               # noqa: E402
import settings_service              # noqa: E402
import sources                       # noqa: E402
import store as store_mod            # noqa: E402


@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path/'loc.db'}")


# --------------------------------------------------------------------------- #
# Resolver normalisation — provider forms in, ONE canonical model out.
# --------------------------------------------------------------------------- #
def test_normalize_country_accepts_names_and_codes():
    assert location.normalize_country("United States") == "US"     # GDELT full name
    assert location.normalize_country("united kingdom") == "GB"
    assert location.normalize_country("us") == "US"                # ISO2, any case
    assert location.normalize_country("GBR") == "GB"               # ISO3
    assert location.normalize_country("Atlantis") is None          # never guessed
    assert location.normalize_country("") is None
    assert location.normalize_country(None) is None


def test_normalize_language_accepts_names_codes_and_tags():
    assert location.normalize_language("English") == "en"          # GDELT full name
    assert location.normalize_language("pt-BR") == "pt"            # BCP-47
    assert location.normalize_language("DE") == "de"
    assert location.normalize_language(None) is None


def test_registry_locality_outranks_provider_country():
    reg = outlet_registry.default_registry()
    # BBC's curated home country (GB) beats a provider claiming US.
    resolved = location.resolve_article_location("United States", "English",
                                                 outlet="bbc.co.uk", registry=reg)
    assert resolved.country == "GB" and resolved.language == "en"
    # Unknown outlet -> the provider's value fills the gap (the GDELT long tail).
    resolved = location.resolve_article_location("United States", "English",
                                                 outlet="smalltownnews.example", registry=reg)
    assert resolved.country == "US"


# --------------------------------------------------------------------------- #
# Phase 0 persistence — additive columns, first-seen merge, search filter.
# --------------------------------------------------------------------------- #
def _upsert(st, canon, country=None, language=None, **over):
    kw = dict(canonical_url=canon, url=canon, publisher=over.pop("publisher", "BBC"),
              source_publisher=None, title=over.pop("title", "t"), description="", body=None,
              published_at=over.pop("published_at", "2026-07-25T10:00:00Z"),
              source_feed="feed", scored=over.pop("scored", {"lean": 0.0, "category": "World"}),
              country=country, language=language)
    kw.update(over)
    return st.upsert_feed_article(**kw)


def test_country_language_persist_and_serialize(st):
    assert _upsert(st, "https://x.test/a", country="GB", language="en") is True
    row = st.get_feed_article("https://x.test/a")
    assert row["country"] == "GB" and row["language"] == "en"


def test_location_backfills_when_empty_and_never_rewrites(st):
    _upsert(st, "https://x.test/a")                                  # no location yet
    _upsert(st, "https://x.test/a", country="GB", language="en")     # merge fills
    row = st.get_feed_article("https://x.test/a")
    assert row["country"] == "GB"
    _upsert(st, "https://x.test/a", country="US")                    # never rewrites first-seen
    assert st.get_feed_article("https://x.test/a")["country"] == "GB"


def test_search_filters_by_country(st):
    """?country= is the EVENT dimension (intended contract change): publisher-located-only
    articles no longer match a country search — publisher home is provenance, not a filter."""
    _upsert(st, "https://x.test/gb", country="GB")
    _upsert(st, "https://x.test/us", country="US", publisher="Fox News")
    st.replace_article_event_locations(
        "https://x.test/gb", location.resolve_event_locations([{"country": "GB", "source": "gdelt-gkg"}]))
    rows, total = st.search_feed_articles(country="gb")
    assert total == 1 and rows[0]["canonicalUrl"] == "https://x.test/gb"
    assert st.search_feed_articles(country="US")[1] == 0             # publisher home never matches
    rows, total = st.search_feed_articles()                          # no filter -> unchanged
    assert total == 2


def test_gdelt_normalize_flows_to_canonical_country(st):
    """The verification the brief asks for, offline: a GDELT artlist payload -> adapter
    normalize -> resolver -> persisted canonical country."""
    payload = {"articles": [{"url": "https://smalltown.example/story", "title": "A story",
                             "seendate": "20260725T101500Z", "domain": "smalltown.example",
                             "language": "English", "sourcecountry": "United States"}]}
    adapter = sources.GDELTAdapter(fetch=lambda url: payload)
    batch = adapter.normalize(adapter.fetch())
    entry = batch.entries[0]
    assert entry.country == "United States" and entry.language == "English"
    loc = location.resolve_article_location(entry.country, entry.language, outlet=entry.publisher_hint)
    assert loc.country == "US" and loc.language == "en"


# --------------------------------------------------------------------------- #
# Registry locality + Local News v1 filtering (handler called directly — plain function).
# --------------------------------------------------------------------------- #
def test_registry_carries_locality_columns():
    reg = outlet_registry.default_registry()
    bbc = reg.resolve("BBC")
    assert bbc.country == "GB" and bbc.scope == "international"
    nyt = reg.resolve("nytimes.com")
    assert nyt.country == "US" and nyt.scope == "national" and nyt.region == "New York"


def test_place_publishers_filters():
    import api_fastapi
    us_national = api_fastapi.place_publishers(country="United States", region=None,
                                               city=None, scope="national")
    assert us_national and all(r["country"] == "US" and r["scope"] == "national" for r in us_national)
    gb = api_fastapi.place_publishers(country="gb", region=None, city=None, scope=None)
    assert {r["name"] for r in gb} >= {"BBC", "The Guardian", "Reuters"}
    with pytest.raises(Exception):
        api_fastapi.place_publishers(country=None, region=None, city=None, scope="galactic")


# --------------------------------------------------------------------------- #
# Settings contract — prepared fields normalize; unknown junk drops.
# --------------------------------------------------------------------------- #
def test_settings_edition_and_locations_normalize():
    out = settings_service.normalize_settings(None, {"edition": "us",
                                                     "locations": [{"placeId": "US", "level": "country"},
                                                                   {"placeId": "", "level": "city"},
                                                                   {"placeId": "US-NY", "level": "orbit"}]})
    assert out["edition"] == "US"
    assert out["locations"] == [{"placeId": "US", "level": "country"}]
    # Defaults stay honest, and legacy stored blobs without the keys read cleanly.
    out = settings_service.normalize_settings({"theme": "dark"})
    assert out["edition"] is None and out["locations"] == []


# --------------------------------------------------------------------------- #
# Geographic Diversity readiness — counted facts, explicit unknowns.
# --------------------------------------------------------------------------- #
def test_reader_geography_counts_countries_and_scope(st):
    _upsert(st, "https://x.test/gb", country="GB", language="en", publisher="BBC")
    _upsert(st, "https://x.test/us", country="US", language="en", publisher="New York Times")
    u = st.upsert_user_by_identity("test", "geo-user")
    st.add_read(u.id, "https://x.test/gb", {"url": "https://x.test/gb", "outlet": "BBC"},
                "2026-07-25T10:00:00Z")
    st.add_read(u.id, "https://x.test/us", {"url": "https://x.test/us", "outlet": "New York Times"},
                "2026-07-25T11:00:00Z")
    st.add_read(u.id, "https://x.test/unknown", {"url": "https://x.test/unknown", "outlet": "Nobody"},
                "2026-07-25T12:00:00Z")
    geo = location.reader_geography(st, u.id)
    assert geo["reads"] == 3 and geo["located"] == 2
    assert geo["countries"] == {"GB": 1, "US": 1}
    assert geo["scope"]["international"] == 1 and geo["scope"]["national"] == 1
    assert geo["scope"]["unknown"] == 1


# --------------------------------------------------------------------------- #
# Phase 1.5 — Countries facts + endpoint merge logic.
# --------------------------------------------------------------------------- #
def test_feed_article_country_facets(st):
    """Facets count the EVENT dimension (intended contract change): publisher-located-only
    articles contribute to no country facet."""
    _upsert(st, "https://x.test/gb1", country="GB", publisher="BBC")
    _upsert(st, "https://x.test/gb2", country="GB", publisher="The Guardian")
    _upsert(st, "https://x.test/us1", country="US", publisher="Fox News")
    _upsert(st, "https://x.test/none")                                # unlocated -> absent
    for url in ("https://x.test/gb1", "https://x.test/gb2"):
        st.replace_article_event_locations(
            url, location.resolve_event_locations([{"country": "GB", "source": "gdelt-gkg"}]))
    facets = st.feed_article_country_facets()
    assert facets == [{"country": "GB", "articles": 2, "publishers": 2}]  # US: publisher-only -> absent


def test_place_countries_unions_registry_and_catalog(st, monkeypatch):
    import api_fastapi
    # An EVENT-located article in a non-registry country (the catalog side of the union).
    _upsert(st, "https://x.test/fr", country="DE", publisher="Le Monde")
    st.replace_article_event_locations(
        "https://x.test/fr", location.resolve_event_locations([{"country": "FR", "source": "gdelt-gkg"}]))
    monkeypatch.setattr(api_fastapi, "_require_store", lambda: st)
    rows = {r["country"]: r for r in api_fastapi.place_countries()}
    assert rows["FR"]["articles"] == 1 and rows["FR"]["registryPublishers"] == 0
    # Registry-only countries appear with honest zero article counts (GB has rated publishers).
    assert rows["GB"]["registryPublishers"] >= 3 and rows["GB"]["articles"] == 0
    # The publisher home (DE) is provenance, not a place facet.
    assert "DE" not in rows or rows["DE"]["articles"] == 0


# --------------------------------------------------------------------------- #
# Phase 2 — event geography: resolver, side table, best-known search/facets.
# --------------------------------------------------------------------------- #
def test_country_names_cover_the_world_with_canonical_codes():
    """The generated full-name table (Phase 2 hardening): real GKG records carry EVERY country's
    English name — long-tail names, legacy spellings, and ASCII-folded diacritics must resolve,
    to CANONICAL codes only (the DD-Germany / AN-Curaçao deprecated-code trap is pinned here)."""
    for name, want in [
        ("Nigeria", "NG"), ("Vietnam", "VN"), ("Tanzania", "TZ"), ("Bolivia", "BO"),
        ("Kazakhstan", "KZ"), ("Myanmar", "MM"), ("Burma", "MM"),               # legacy spelling
        ("Ivory Coast", "CI"), ("Cote d'Ivoire", "CI"),                         # ASCII fold
        ("Democratic Republic of the Congo", "CD"), ("Republic of the Congo", "CG"),
        ("Kosovo", "XK"), ("Saint Kitts and Nevis", "KN"), ("Laos", "LA"),
        ("Germany", "DE"), ("Gambia", "GM"),          # FIPS would say GM=Germany — names never do
        ("Curacao", "CW"), ("Atlantis", None),
    ]:
        assert location.normalize_country(name) == want, name
    # The generated table itself only ever emits canonical assigned codes (regression pin for
    # the deprecated-code trap: ICU resolves DD/AN/DY etc. — the generator must exclude them).
    assert location._COUNTRY_NAMES_FULL["germany"] == "DE"
    assert location._COUNTRY_NAMES_FULL["curacao"] == "CW"
    assert location._COUNTRY_NAMES_FULL["benin"] == "BJ"


def test_resolve_event_locations_normalizes_and_fails_honest():
    events = location.resolve_event_locations([
        {"country": "France", "city": "Paris", "lat": "48.85", "lon": 2.35, "source": "gdelt-gkg"},
        {"country": "FRA"},                       # ISO3 → dedupes against nothing (no city)
        {"country": "France", "city": "Paris"},   # duplicate (country, region, city) → dropped
        {"country": "Atlantis"},                  # unresolvable → dropped, never guessed
        "not-a-mapping",                          # malformed → dropped
    ])
    assert [(e.country, e.city, e.source) for e in events] == [
        ("FR", "Paris", "gdelt-gkg"), ("FR", None, "provider")]
    assert events[0].lat == 48.85 and events[0].lon == 2.35
    assert location.resolve_event_locations(None) == ()


def test_event_locations_roundtrip_and_per_source_replace(st):
    _upsert(st, "https://x.test/a", country="US")
    st.replace_article_event_locations(
        "https://x.test/a", location.resolve_event_locations(
            [{"country": "France", "source": "gdelt-gkg"}, {"country": "DE", "source": "georss"}]))
    assert st.event_countries_for_urls(["https://x.test/a"]) == {"https://x.test/a": ["DE", "FR"]}
    # Re-ingest from ONE source replaces only that source's rows (backfill discipline).
    st.replace_article_event_locations(
        "https://x.test/a", location.resolve_event_locations([{"country": "IT", "source": "gdelt-gkg"}]))
    assert st.event_countries_for_urls(["https://x.test/a"]) == {"https://x.test/a": ["DE", "IT"]}
    # Empty input never wipes stored facts; unknown URLs are simply absent.
    st.replace_article_event_locations("https://x.test/a", ())
    assert st.event_countries_for_urls(["https://x.test/a", "https://x.test/none"]) == {
        "https://x.test/a": ["DE", "IT"]}


def test_search_uses_event_location_only(st):
    # A: US publisher, event in FR → matches FR only. B: US publisher, no events → matches
    # NOTHING (appears only unfiltered): publisher home is never a content-filter substitute.
    _upsert(st, "https://x.test/a", country="US", publisher="CNN")
    _upsert(st, "https://x.test/b", country="US", publisher="Fox News")
    st.replace_article_event_locations(
        "https://x.test/a", location.resolve_event_locations([{"country": "FR", "source": "gdelt-gkg"}]))
    rows, total = st.search_feed_articles(country="fr")
    assert total == 1 and rows[0]["canonicalUrl"] == "https://x.test/a"
    assert st.search_feed_articles(country="US")[1] == 0
    assert st.search_feed_articles()[1] == 2                         # no filter → the whole feed


def test_country_facets_are_event_dimension(st):
    _upsert(st, "https://x.test/a", country="US", publisher="CNN")       # event FR → counts FR
    _upsert(st, "https://x.test/b", country="US", publisher="Fox News")  # no events → counts nowhere
    st.replace_article_event_locations(
        "https://x.test/a", location.resolve_event_locations([{"country": "FR", "source": "gdelt-gkg"}]))
    facets = {f["country"]: f for f in st.feed_article_country_facets()}
    assert facets == {"FR": {"country": "FR", "articles": 1, "publishers": 1}}
    # publisher-located-only catalog → honestly EMPTY facets (pickers offer nothing, not wrong things)
    st2 = store_mod.Store("sqlite://")
    _upsert(st2, "https://x.test/c", country="US")
    assert st2.feed_article_country_facets() == []


def test_ingest_persists_event_locations(st):
    """FeedEntry.event_locations → resolver → side table, through the real ingest path."""
    import rss_ingest
    entry = rss_ingest.FeedEntry(
        url="https://smalltown.example/quake", title="Earthquake shakes the coast",
        description="A quake", published_at="2026-07-25T10:00:00Z",
        country="United States",
        event_locations=({"country": "Japan", "city": "Sendai", "source": "gdelt-gkg"},))
    rss_ingest.ingest_entries([entry], "Smalltown", "feed://x", rss_ingest.make_scorer(), st)
    facets = {f["country"]: f for f in st.feed_article_country_facets()}
    assert "JP" in facets and facets["JP"]["articles"] == 1
