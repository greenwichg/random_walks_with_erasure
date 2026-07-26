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
    _upsert(st, "https://x.test/gb", country="GB")
    _upsert(st, "https://x.test/us", country="US", publisher="Fox News")
    rows, total = st.search_feed_articles(country="gb")
    assert total == 1 and rows[0]["canonicalUrl"] == "https://x.test/gb"
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
    _upsert(st, "https://x.test/gb1", country="GB", publisher="BBC")
    _upsert(st, "https://x.test/gb2", country="GB", publisher="The Guardian")
    _upsert(st, "https://x.test/us1", country="US", publisher="Fox News")
    _upsert(st, "https://x.test/none")                                # unlocated -> absent
    facets = st.feed_article_country_facets()
    assert facets[0] == {"country": "GB", "articles": 2, "publishers": 2}
    assert {f["country"] for f in facets} == {"GB", "US"}


def test_place_countries_unions_registry_and_catalog(st, monkeypatch):
    import api_fastapi
    _upsert(st, "https://x.test/fr", country="FR", publisher="Le Monde")   # catalog-only country
    monkeypatch.setattr(api_fastapi, "_require_store", lambda: st)
    rows = {r["country"]: r for r in api_fastapi.place_countries()}
    assert rows["FR"]["articles"] == 1 and rows["FR"]["registryPublishers"] == 0
    # Registry-only countries appear with honest zero article counts (GB has rated publishers).
    assert rows["GB"]["registryPublishers"] >= 3 and rows["GB"]["articles"] == 0
