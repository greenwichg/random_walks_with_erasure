"""examples/publisher_metadata.py — the curated/enriched merge, and its provenance contract.

The load-bearing rule: **curated data is never overwritten.** Enrichment fills gaps and nothing
else. These tests pin that from both directions (curated wins where it exists; enrichment appears
only where it doesn't), plus the cache asymmetry that stops a bad minute upstream from emptying
publisher pages.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import media                     # noqa: E402
import outlet_registry           # noqa: E402
import publisher_metadata as pm  # noqa: E402
import publisher_service as ps   # noqa: E402
import store as store_mod        # noqa: E402


def _outlet(**kw):
    base = dict(canonical="Example Post", lean=0.0, country=None, region=None, city=None,
                scope=None)
    base.update(kw)
    return outlet_registry.Outlet(**base)


def _cached(**kw):
    base = {"status": "ok", "source": "wikipedia", "description": None, "founded": None,
            "headquarters": None, "country": None, "website": None, "parent": None,
            "logo": None, "logoSource": None, "wikipediaUrl": None,
            "fetchedAt": "2026-07-27T10:00:00+00:00"}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# The merge rule.
# --------------------------------------------------------------------------- #
def test_curated_country_is_not_overwritten_by_wikidata():
    out = pm.merge(_outlet(country="GB"), _cached(country="US"))
    assert out["country"] == "GB" and out["sources"]["country"] == "curated"


def test_curated_headquarters_is_composed_from_registry_city_and_region():
    out = pm.merge(_outlet(city="London", region="England"), _cached(headquarters="Salford"))
    assert out["headquarters"] == "London, England"
    assert out["sources"]["headquarters"] == "curated"


def test_enrichment_fills_only_the_gaps_the_registry_leaves():
    out = pm.merge(_outlet(country="GB"),
                   _cached(country="US", founded="1821", parent="Example Group",
                           description="A daily paper."))
    assert out["country"] == "GB"                       # curated held
    assert out["founded"] == "1821" and out["sources"]["founded"] == "wikimedia"
    assert out["parent"] == "Example Group"
    assert out["description"] == "A daily paper." and out["sources"]["description"] == "wikipedia"


def test_counted_website_beats_wikidata_and_is_labelled_counted():
    """The host we actually observe them publishing from outranks a claim about it."""
    out = pm.merge(_outlet(), _cached(website="https://old.example.com"),
                   site="https://examplepost.com")
    assert out["website"] == "https://examplepost.com"
    assert out["sources"]["website"] == "counted"


def test_wikidata_website_is_used_when_the_catalog_has_no_host():
    out = pm.merge(_outlet(), _cached(website="https://examplepost.com"), site=None)
    assert out["website"] == "https://examplepost.com"
    assert out["sources"]["website"] == "wikimedia"


def test_an_unusable_status_contributes_no_facts_but_still_reports_itself():
    """A recorded miss is information — it distinguishes "we looked and found nothing" from
    "nobody has looked yet", which is what makes a stale cache visible instead of silent."""
    out = pm.merge(_outlet(), _cached(status="ambiguous", founded="1900", description="Nope."))
    assert "founded" not in out and "description" not in out
    assert out["status"] == "ambiguous" and out["refreshedAt"] == "2026-07-27T10:00:00+00:00"


def test_a_wikipedia_link_is_only_offered_for_a_verified_match():
    ok = pm.merge(_outlet(), _cached(founded="1821", wikipediaUrl="https://en.wikipedia.org/wiki/X"))
    assert ok["wikipediaUrl"] == "https://en.wikipedia.org/wiki/X"
    bad = pm.merge(_outlet(), _cached(status="ambiguous",
                                      wikipediaUrl="https://en.wikipedia.org/wiki/X"))
    assert "wikipediaUrl" not in bad


def test_nothing_known_yields_an_empty_block_not_a_wall_of_nulls():
    assert pm.merge(None, None) == {}
    assert pm.merge(_outlet(), None) == {}


def test_none_fields_are_omitted_rather_than_serialized_as_null():
    out = pm.merge(_outlet(country="GB"), _cached())
    assert set(out) == {"country", "sources", "status", "refreshedAt"}


# --------------------------------------------------------------------------- #
# Cache replacement asymmetry.
# --------------------------------------------------------------------------- #
def test_a_successful_lookup_replaces_the_cached_row():
    assert pm.should_replace(_cached(founded="1821"), {"status": "ok", "founded": "1822"}) is True


def test_a_transport_failure_never_wipes_verified_facts():
    """An error means the request did not complete — it says nothing about the outlet, so it must
    not throw away facts verified an hour ago."""
    assert pm.should_replace(_cached(founded="1821"), {"status": "error"}) is False


def test_a_corrected_refusal_does_replace_a_wrong_success():
    """The bug this rule originally had, pinned so it cannot come back. "ABC News" was cached ok
    against an Albanian broadcaster; when the verification bug was fixed, the corrected refusal was
    DISCARDED and the wrong row kept, because refusal is not success. A module whose rule is "a
    wrong match is worse than no match" cannot also refuse to un-match."""
    wrong = _cached(founded="1998", website="http://www.abcnews.al")
    assert pm.should_replace(wrong, {"status": "ambiguous", "reason": "domain_conflict"}) is True
    assert pm.should_replace(wrong, {"status": "no_match", "reason": "no_page"}) is True


def test_a_failed_lookup_does_replace_a_previous_failure():
    """Otherwise a no_match could never become ambiguous, and the retry cadence would be wrong."""
    assert pm.should_replace(_cached(status="no_match"), {"status": "ambiguous"}) is True
    assert pm.should_replace(None, {"status": "no_match"}) is True


# --------------------------------------------------------------------------- #
# Logo precedence.
# --------------------------------------------------------------------------- #
def test_logo_precedence_is_curated_then_enriched_then_site_icon():
    enriched = ("https://commons.wikimedia.org/logo.svg", "wikimedia")
    site_icon = media.pick_best_logo("Example Post", "https://examplepost.com")
    assert site_icon["publisherLogoSource"] == "site-icon"
    # The 16px favicon is still offered, but last — never the first thing shown.
    assert site_icon["publisherLogoFallbacks"][-1].endswith("/favicon.ico")

    with_wiki = media.pick_best_logo("Example Post", "https://examplepost.com", enriched=enriched)
    assert with_wiki["publisherLogo"] == enriched[0]
    assert with_wiki["publisherLogoSource"] == "wikimedia"

    media._CURATED_LOGOS["example post"] = {"logo": "https://cdn.example/curated.svg"}
    try:
        curated = media.pick_best_logo("Example Post", "https://examplepost.com",
                                       enriched=enriched)
        assert curated["publisherLogo"] == "https://cdn.example/curated.svg"
        assert curated["publisherLogoSource"] == "registry"
    finally:
        media._CURATED_LOGOS.pop("example post", None)


def test_a_non_http_enriched_logo_is_refused_and_falls_through():
    """The same absolute-URL guard every other media URL passes — a relative or javascript: value
    never reaches an img src."""
    out = media.pick_best_logo("Example Post", "https://examplepost.com",
                               enriched=("/relative/logo.png", "wikimedia"))
    assert out["publisherLogoSource"] == "site-icon"


def test_logo_from_cache_ignores_unusable_rows():
    assert pm.logo_from_cache(_cached(status="ambiguous", logo="https://x/logo.svg")) is None
    assert pm.logo_from_cache(None) is None
    assert pm.logo_from_cache(_cached(logo="https://x/logo.svg", logoSource="wikipedia")) == (
        "https://x/logo.svg", "wikipedia")


# --------------------------------------------------------------------------- #
# Profile integration.
# --------------------------------------------------------------------------- #
def _seed(st, publisher="Example Post", host="examplepost.com"):
    st.upsert_feed_article(
        canonical_url=f"https://{host}/a", url=f"https://{host}/a", publisher=publisher,
        source_publisher=publisher, title="A headline about something", description="d", body=None,
        published_at="2026-07-20T09:00:00+00:00", source_feed="f",
        scored={"article_id": f"https://{host}/a", "outlet": publisher, "category": "Politics",
                "lean": 0.0, "political": True, "title": "A headline about something"})


def test_profile_renders_without_any_enrichment():
    """The requirement that matters most operationally: no Wikipedia match must never break a page."""
    st = store_mod.Store("sqlite://")
    _seed(st)
    profile = ps.get_publisher(st, "Example Post")
    assert profile is not None and profile["articles"]["total"] == 1
    assert profile.get("about", {}).get("status") is None      # nothing looked up yet
    assert profile["publisherLogoSource"] == "site-icon"         # still has a logo


def test_profile_exposes_enriched_facts_with_provenance():
    st = store_mod.Store("sqlite://")
    _seed(st)
    st.upsert_publisher_metadata(
        "Example Post", status="ok", source="wikipedia", founded="1821", parent="Example Group",
        description="A daily paper.", logo="https://commons.wikimedia.org/l.svg",
        logo_source="wikimedia", wikipedia_url="https://en.wikipedia.org/wiki/Example_Post")
    profile = ps.get_publisher(st, "Example Post")
    about = profile["about"]
    assert about["founded"] == "1821" and about["sources"]["founded"] == "wikimedia"
    assert about["wikipediaUrl"].endswith("Example_Post")
    assert profile["publisherLogoSource"] == "wikimedia"


def test_a_recorded_miss_leaves_the_page_intact():
    st = store_mod.Store("sqlite://")
    _seed(st)
    st.upsert_publisher_metadata("Example Post", status="no_match")
    profile = ps.get_publisher(st, "Example Post")
    assert profile["about"]["status"] == "no_match"
    assert profile["publisherLogoSource"] == "site-icon"         # falls back, never blank
    assert profile["articles"]["total"] == 1


# --------------------------------------------------------------------------- #
# Schema upgrade on an existing DB.
# --------------------------------------------------------------------------- #
def test_a_database_predating_the_reason_column_is_upgraded_in_place(tmp_path):
    """Regression, from production. `Base.metadata.create_all` creates NEW tables only — it never
    adds a column to a table that already exists. publisher_metadata shipped in one deploy and
    gained `reason` in the next, so the live DB kept the original schema and every read failed with
    "no such column: publisher_metadata.reason". Any column added after a table's first deploy needs
    an _ensure_* entry, and this proves that path works rather than assuming it."""
    import sqlite3
    db = tmp_path / "legacy.db"
    # Build the table exactly as the FIRST deploy created it: no `reason`, no `logo_source`.
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE publisher_metadata (
        publisher_key VARCHAR(255) NOT NULL PRIMARY KEY, publisher VARCHAR(255) NOT NULL,
        status VARCHAR(16), source VARCHAR(16), wikidata_id VARCHAR(32),
        wikipedia_title VARCHAR(255), wikipedia_url VARCHAR(1024), description TEXT,
        founded VARCHAR(32), headquarters VARCHAR(255), country VARCHAR(2),
        website VARCHAR(1024), parent VARCHAR(255), logo VARCHAR(1024), error TEXT,
        fetched_at DATETIME)""")
    con.execute("INSERT INTO publisher_metadata (publisher_key, publisher, status, founded) "
                "VALUES ('legacy outlet', 'Legacy Outlet', 'ok', '1900')")
    con.commit()
    con.close()

    st = store_mod.Store(f"sqlite:///{db}")

    row = st.publisher_metadata("Legacy Outlet")          # would raise OperationalError before
    assert row["status"] == "ok" and row["founded"] == "1900"
    assert row["reason"] is None and row["logoSource"] is None    # new columns, honestly empty
    # …and the upgraded table accepts writes to the new columns.
    st.upsert_publisher_metadata("Legacy Outlet", status="ambiguous", reason="domain_conflict")
    assert st.publisher_metadata("Legacy Outlet")["reason"] == "domain_conflict"


def test_the_ensure_pass_is_idempotent(tmp_path):
    """It runs on every Store construction, so a second open must be a no-op, not an error."""
    db = tmp_path / "twice.db"
    store_mod.Store(f"sqlite:///{db}").upsert_publisher_metadata("X", status="ok", reason="domain")
    assert store_mod.Store(f"sqlite:///{db}").publisher_metadata("X")["reason"] == "domain"
