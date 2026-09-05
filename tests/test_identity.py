"""Durable identity + provenance + licence class at the store and ingest boundary.

docs/NEWS_INTELLIGENCE_INFRASTRUCTURE.md §E.1 / §E.3: an article id is minted once and every URL
form resolves to it; a publisher id is a pure function of the outlet's identity key; provenance
records every (channel, source) an article came through; the licence class only ever widens.
"""

import pathlib
import sys

import pytest
from sqlalchemy import text

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import identity  # noqa: E402
import identity_backfill  # noqa: E402
import ingest  # noqa: E402
import licence  # noqa: E402
import rss_ingest  # noqa: E402
import store as store_mod  # noqa: E402


@pytest.fixture
def st():
    return store_mod.Store("sqlite:///:memory:")


@pytest.fixture
def scorer():
    return rss_ingest.make_scorer()


def _entry(url, title="Prime minister resigns after vote", **kw):
    return rss_ingest.FeedEntry(url=url, title=title, published_at="2026-09-01T10:00:00+00:00", **kw)


def _ingest(st, scorer, entries, *, source_type="rss", feed="https://feeds.bbci.co.uk/news/rss.xml",
            name="BBC feed"):
    return rss_ingest.ingest_entries(entries, name, feed, scorer, st, source_type=source_type)


# ---- article ids --------------------------------------------------------------------------- #

def test_article_id_is_deterministic_and_opaque():
    a = store_mod.article_id_for("https://x.example/a")
    assert a == store_mod.article_id_for("https://x.example/a") == identity.article_id_for("https://x.example/a")
    assert a.startswith("ar_") and len(a) == 23
    assert a != store_mod.article_id_for("https://x.example/b")


def test_ingest_assigns_id_aliases_provenance_and_licence(st, scorer):
    raw = "https://www.bbc.co.uk/news/articles/abc123?utm_source=x"
    s = _ingest(st, scorer, [_entry(raw, publisher_hint="bbc.co.uk")])
    assert s["new"] == 1
    row = st.resolve_article(raw)
    assert row is not None and row["articleId"].startswith("ar_")
    assert row["licenceClass"] == "metadata_public"          # RSS = the publisher's own offer
    assert row["scorerVersion"] == "1"
    assert row["publisherId"] == identity.publisher_id_for("BBC")
    # every form resolves: the raw url, the canonical url, the id
    canonical = row["canonicalUrl"]
    assert canonical != raw
    for ref in (raw, canonical, row["articleId"]):
        assert st.resolve_article(ref)["articleId"] == row["articleId"]
    prov = st.article_provenance(canonical)
    assert [(p["channel"], p["licenceClass"], p["observations"]) for p in prov] == [
        ("rss", "metadata_public", 1)]
    assert prov[0]["sourceRef"] == "https://feeds.bbci.co.uk/news/rss.xml"


def test_repoll_counts_observations_and_a_new_channel_adds_a_row(st, scorer):
    url = "https://www.bbc.co.uk/news/articles/abc123"
    _ingest(st, scorer, [_entry(url, publisher_hint="bbc.co.uk")])
    _ingest(st, scorer, [_entry(url, publisher_hint="bbc.co.uk")])      # same feed again
    _ingest(st, scorer, [_entry(url, publisher_hint="bbc.co.uk", source_type="newsapi",
                                source_provider="NewsAPI")], source_type="newsapi", feed="newsapi")
    prov = st.article_provenance(st.resolve_article(url)["canonicalUrl"])
    assert [(p["channel"], p["observations"]) for p in prov] == [("rss", 2), ("newsapi", 1)]
    assert prov[0]["firstObservedAt"] <= prov[0]["lastObservedAt"]


def test_licence_class_only_widens(st, scorer):
    url = "https://www.npr.org/2026/09/01/pm-resigns"
    _ingest(st, scorer, [_entry(url, publisher_hint="npr.org", source_type="newsapi",
                                source_provider="NewsAPI")], source_type="newsapi", feed="newsapi")
    assert st.resolve_article(url)["licenceClass"] == "provider_restricted"
    _ingest(st, scorer, [_entry(url, publisher_hint="npr.org")])          # the publisher's own feed
    assert st.resolve_article(url)["licenceClass"] == "metadata_public"
    _ingest(st, scorer, [_entry(url, publisher_hint="npr.org", source_type="newsapi",
                                source_provider="NewsAPI")], source_type="newsapi", feed="newsapi")
    assert st.resolve_article(url)["licenceClass"] == "metadata_public"   # never narrows back


def test_extension_born_articles_are_reader_private(st, scorer):
    url = "https://www.npr.org/2026/09/01/read-by-one-reader"
    _ingest(st, scorer, [_entry(url, publisher_hint="npr.org", source_type="extension")],
            source_type="extension", feed="extension", name=None)
    row = st.resolve_article(url)
    assert row["articleState"] == "provisional" and row["licenceClass"] == "reader_private"
    _ingest(st, scorer, [_entry(url, publisher_hint="npr.org")])
    row = st.resolve_article(url)
    assert row["articleState"] == "verified" and row["licenceClass"] == "metadata_public"


def test_repoll_heals_a_legacy_row_without_an_id(st, scorer):
    url = "https://www.bbc.co.uk/news/articles/legacy"
    _ingest(st, scorer, [_entry(url, publisher_hint="bbc.co.uk")])
    canonical = st.resolve_article(url)["canonicalUrl"]
    with st.session() as s:                                     # a row from before identity existed
        s.execute(text("UPDATE feed_articles SET article_id=NULL, publisher_id=NULL, "
                       "licence_class=NULL, scorer_version=NULL WHERE canonical_url=:u"), {"u": canonical})
    assert st.get_feed_article(canonical)["articleId"] is None
    _ingest(st, scorer, [_entry(url, publisher_hint="bbc.co.uk")])
    row = st.get_feed_article(canonical)
    assert row["articleId"] == store_mod.article_id_for(canonical)
    assert row["publisherId"] and row["licenceClass"] == "metadata_public" and row["scorerVersion"] == "1"


def test_alias_lookup_is_batched_by_url_form(st, scorer):
    raw = "https://www.bbc.co.uk/news/articles/abc123?utm_source=x"
    _ingest(st, scorer, [_entry(raw, publisher_hint="bbc.co.uk")])
    row = st.resolve_article(raw)
    ids = st.article_ids_for_urls([raw, row["canonicalUrl"], "https://nowhere.example/x"])
    assert ids == {raw: row["articleId"], row["canonicalUrl"]: row["articleId"]}
    meta = st.article_meta_for_urls([raw])
    assert meta[raw]["licenceClass"] == "metadata_public" and meta[raw]["articleId"] == row["articleId"]


# ---- publisher ids ------------------------------------------------------------------------- #

def test_publisher_id_is_stable_across_name_forms():
    assert identity.publisher_id_for("BBC") == identity.publisher_id_for("bbc.co.uk") \
        == identity.publisher_id_for("BBC News") == identity.publisher_id_for("https://www.bbc.com/news/x")
    assert identity.publisher_identity_key("BBC") == "c:BBC"
    assert identity.publisher_id_for("BBC").startswith("pub_")
    assert identity.publisher_id_for("") is None and identity.publisher_id_for(None) is None


def test_unregistered_hosts_fold_on_the_brand_domain():
    assert identity.publisher_identity_key("kfbk.examplebrand.net") == "d:examplebrand.net"
    a = identity.publisher_id_for("kfbk.examplebrand.net")
    assert a == identity.publisher_id_for("wjjs.examplebrand.net") \
        == identity.publisher_id_for("https://www.examplebrand.net/news/x")
    assert identity.publisher_identity_key("Some Local Paper") == "n:somelocalpaper"
    assert identity.publisher_id_for("Some Local Paper") != a
    # a registered host folds on the registry canonical, whatever subdomain the feed used
    assert identity.publisher_identity_key("kfbk.iheart.com") == "c:iHeartRadio"


def test_sync_publishers_materialises_the_registry(st, scorer):
    _ingest(st, scorer, [_entry("https://www.bbc.co.uk/news/articles/abc123", publisher_hint="bbc.co.uk")])
    stats = identity.sync_publishers(st)
    assert stats["created"] + stats["updated"] >= 600 and stats["registryVersion"].startswith("sha256:")
    row = st.publisher_by_id(identity.publisher_id_for("BBC"))
    assert row["registered"] is True and row["name"] == "BBC" and row["country"] == "GB"
    assert row["lean"] == 0.0 and row["leanSource"] == "allsides"
    assert row["articles"] == 1 and row["registryVersion"] == stats["registryVersion"]
    assert "bbc.co.uk" in st.publisher_hosts(row["publisherId"])
    assert st.publisher_for_host("bbc.co.uk")["publisherId"] == row["publisherId"]
    # idempotent
    again = identity.sync_publishers(st)
    assert again["created"] == 0
    assert st.upsert_publisher(publisher_id="pub_nope", identity_key="", name="", create=False) is None


def test_registry_version_tracks_the_file(tmp_path):
    p = tmp_path / "reg.csv"
    p.write_text("canonical,lean,aliases\nA,0,a.example\n")
    v1 = identity.registry_version(str(p))
    p.write_text("canonical,lean,aliases\nA,1,a.example\n")
    v2 = identity.registry_version(str(p))
    assert v1 != v2 and v1.startswith("sha256:")
    assert identity.registry_version(str(tmp_path / "missing.csv")) == "unknown"


# ---- the backfill -------------------------------------------------------------------------- #

def test_backfill_fills_legacy_rows_and_is_idempotent(st, scorer):
    raw = [f"https://www.bbc.co.uk/news/articles/legacy{i}" for i in range(5)]
    _ingest(st, scorer, [_entry(u, publisher_hint="bbc.co.uk") for u in raw])
    urls = [ingest.canonical_url(u) for u in raw]
    with st.session() as s:
        s.execute(text("UPDATE feed_articles SET article_id=NULL, publisher_id=NULL, licence_class=NULL"))
        s.execute(text("DELETE FROM article_aliases"))
        s.execute(text("DELETE FROM article_provenance"))
        s.execute(text("DELETE FROM publishers"))
    dry = identity_backfill.run(st, batch=2, dry_run=True, log=lambda *_: None)
    assert dry["rows"] == 5 and dry["missingArticleId"] == 5 and dry["changed"] == 0
    assert st.get_feed_article(urls[0])["articleId"] is None            # dry run wrote nothing
    res = identity_backfill.run(st, batch=2, log=lambda *_: None)
    assert res["changed"] == 5 and res["batches"] == 3 and res["publishers"]["created"] >= 600
    for u in urls:
        row = st.get_feed_article(u)
        assert row["articleId"] == store_mod.article_id_for(u)
        assert row["publisherId"] == identity.publisher_id_for("BBC")
        assert row["licenceClass"] == "metadata_public"
        assert st.resolve_article(row["articleId"])["canonicalUrl"] == u
        prov = st.article_provenance(u)
        assert len(prov) == 1 and prov[0]["channel"] == "rss" and prov[0]["observations"] == 1
    again = identity_backfill.run(st, batch=10, publishers=False, log=lambda *_: None)
    assert again["changed"] == 0 and again["missingArticleId"] == 0


def test_orphan_reapers_follow_the_catalogue(st, scorer):
    url = "https://www.bbc.co.uk/news/articles/gone"
    _ingest(st, scorer, [_entry(url, publisher_hint="bbc.co.uk")])
    st.delete_feed_articles([ingest.canonical_url(url)])
    assert st.prune_orphan_provenance() == 1
    assert st.prune_orphan_aliases() == 2                     # the raw form and the canonical form
    assert st.prune_orphan_provenance() == 0 and st.prune_orphan_aliases() == 0


def test_licence_vocabulary_is_shared():
    assert licence.LICENCE_CLASSES == store_mod.LICENCE_CLASSES
    assert licence.merge("provider_restricted", "metadata_public") == "metadata_public"
    assert licence.merge(None, "unknown") == "unknown" and licence.merge(None, None) is None
