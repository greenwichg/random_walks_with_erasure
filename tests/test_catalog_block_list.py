"""The outlet-level catalog block list — `RWE_CATALOG_BLOCKED_OUTLETS`.

The properties worth pinning are the ones a block list is bought for: that it matches on the
RESOLVED identity rather than on whichever name string happened to arrive, that it stops the
article before it reaches the catalog, and — most important of all — that an unset setting changes
nothing. A filter on the one path every producer funnels through is exactly the place where a
mistake silently costs a whole feed.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import enrich          # noqa: E402
import ingest          # noqa: E402
import rss_ingest      # noqa: E402
import store as store_mod   # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Both spellings cleared per test — a value leaking in from the environment would make these
    pass or fail for a reason that has nothing to do with the code."""
    for key in ingest._BLOCKED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    ingest._blocked_index.cache_clear()
    yield
    ingest._blocked_index.cache_clear()


@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path / 'catalog.db'}")


def _entry(url, publisher_hint, title="Council approves the harbour dredging schedule"):
    return rss_ingest.FeedEntry(
        url=url, title=title, description="d" * 60,
        published_at="2026-08-10T09:00:00+00:00", publisher_hint=publisher_hint)


def _ingest(st, *entries):
    scorer = ingest.Scorer(enricher=enrich.make_enricher("baseline"))
    return rss_ingest.ingest_entries(list(entries), None, "test", scorer, st)


def test_an_unset_setting_blocks_nothing(st):
    """The default. Every deployment that sets nothing must behave exactly as it did before this
    existed — which is the whole reason the check returns before doing any work when unset."""
    stats = _ingest(st,
                    _entry("https://www.bbc.co.uk/news/a1", "BBC News"),
                    _entry("https://sportskeeda.com/x/a2", "Sportskeeda"))
    assert stats["blocked"] == 0
    assert stats["new"] == 2, "both articles entered the catalog"


def test_a_blocked_outlet_is_matched_by_identity_not_by_the_string_that_arrived(st, monkeypatch):
    """The requirement that makes this worth building. The setting names a DOMAIN; the article
    arrives labelled with a display name. A raw-string filter would miss it; resolving both through
    the registry catches it, because they are the same outlet."""
    monkeypatch.setenv("RWE_CATALOG_BLOCKED_OUTLETS", "bbc.co.uk")
    ingest._blocked_index.cache_clear()

    canonicals, hosts = ingest.blocked_catalog_index()
    assert canonicals and not hosts, "a registered domain resolves to an identity, not a host rule"

    stats = _ingest(st,
                    _entry("https://www.bbc.co.uk/news/a1", "BBC News"),
                    _entry("https://www.reuters.com/x/a2", "Reuters"))
    assert stats["blocked"] == 1
    assert stats["new"] == 1, "the other outlet is untouched"


def test_identity_matching_works_from_either_form(st, monkeypatch):
    """The other direction, which is the one a host-only filter cannot do: the setting names the
    OUTLET and the article carries no publisher hint at all, so the only thing to resolve is its
    URL. Both sides land on the same canonical, so it is blocked."""
    monkeypatch.setenv("RWE_CATALOG_BLOCKED_OUTLETS", "BBC News")
    ingest._blocked_index.cache_clear()
    canonicals, hosts = ingest.blocked_catalog_index()
    assert canonicals and not hosts, "a registered NAME is an identity rule, never a host rule"

    stats = _ingest(st,
                    _entry("https://www.bbc.co.uk/news/a1", ""),      # no hint — resolved from URL
                    _entry("https://www.reuters.com/x/a2", ""))
    assert stats["blocked"] == 1
    assert stats["new"] == 1


def test_an_unregistered_outlet_is_blocked_by_domain_subdomains_included(st, monkeypatch):
    """The common case: the outlets worth keeping out are usually the ones with no registry row.
    They have no canonical identity, so the domain IS the identity — and it has to be
    subdomain-tolerant, or a feed simply moves to news.<domain> and walks straight back in."""
    monkeypatch.setenv("RWE_CATALOG_BLOCKED_OUTLETS", "contentfarm.example")
    ingest._blocked_index.cache_clear()

    stats = _ingest(st,
                    _entry("https://contentfarm.example/a1", "Content Farm"),
                    _entry("https://news.contentfarm.example/a2", "Content Farm Wire"),
                    _entry("https://notcontentfarm.example/a3", "Not The Content Farm"))
    assert stats["blocked"] == 2, "the bare domain and its subdomain"
    assert stats["new"] == 1, "a domain that merely ENDS with the same letters is not a subdomain"


def test_a_blocked_article_never_reaches_the_catalog(st, monkeypatch):
    """`blocked` counting up is not the contract — the article being absent is."""
    monkeypatch.setenv("RWE_CATALOG_BLOCKED_OUTLETS", "contentfarm.example")
    ingest._blocked_index.cache_clear()
    _ingest(st,
            _entry("https://contentfarm.example/a1", "Content Farm"),
            _entry("https://www.reuters.com/x/a2", "Reuters"))

    rows, total = st.search_feed_articles(sort="newest")
    publishers = {(r.get("publisher") or "") for r in rows}
    assert total == 1 and "Content Farm" not in publishers
    assert any("Reuters" in p for p in publishers), f"only the allowed outlet is stored: {publishers}"


def test_the_unprefixed_spelling_is_accepted_too(monkeypatch):
    monkeypatch.setenv("CATALOG_BLOCKED_OUTLETS", "contentfarm.example")
    ingest._blocked_index.cache_clear()
    assert ingest.is_blocked_from_catalog("Content Farm", "https://contentfarm.example/a1")


def test_an_entry_that_is_neither_a_row_nor_a_domain_is_visibly_understood_as_nothing(monkeypatch):
    """A bare unregistered name has no identity to match and no domain to match on. It cannot be
    honoured, and `blocked_catalog_index` is what makes that discoverable instead of a block list
    that quietly does nothing."""
    monkeypatch.setenv("RWE_CATALOG_BLOCKED_OUTLETS", "Some Blog Nobody Curated")
    ingest._blocked_index.cache_clear()
    canonicals, hosts = ingest.blocked_catalog_index()
    assert not canonicals and not hosts
    assert not ingest.is_blocked_from_catalog("Some Blog Nobody Curated", "https://someblog.example/a")
