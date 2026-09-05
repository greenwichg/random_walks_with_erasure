"""Term search: the FTS5 index behind ``q`` on the platform — and the consumer path left as it was.

The index (``feed_articles_fts``) is created by the store, kept in step by triggers through every
writer, and searched with every word required in any order, stemmed, ranked by bm25 with the
headline weighted. The consumer surfaces keep the substring match unless ``RWE_SEARCH_TERMS=1``.
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import rss_ingest  # noqa: E402
import search  # noqa: E402
import search_index  # noqa: E402
import store as store_mod  # noqa: E402
import story_service  # noqa: E402
from sqlalchemy import delete  # noqa: E402

E = rss_ingest.FeedEntry


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("RWE_SEARCH_TERMS", raising=False)
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "36500")
    story_service.clear_cache()
    yield
    story_service.clear_cache()


def _seed(url="sqlite:///:memory:"):
    st = store_mod.Store(url)
    scorer = rss_ingest.make_scorer()
    rss_ingest.ingest_entries([
        E(url="https://www.bbc.co.uk/news/1", title="Trump asks Apple to change Lake Ontario name",
          published_at="2026-09-01T10:00:00+00:00", description="A request to the company.",
          publisher_hint="bbc.co.uk"),
        E(url="https://www.theguardian.com/us/2", title="Senate advances budget package after late-night vote",
          published_at="2026-09-01T11:00:00+00:00", description="Apple was not mentioned.",
          publisher_hint="theguardian.com"),
        E(url="https://www.npr.org/3", title="Prime minister resigns after confidence vote",
          published_at="2026-09-01T12:00:00+00:00", description="Westminster reacts.", publisher_hint="npr.org"),
        E(url="https://www.reuters.com/4", title="Apple unveils new iPhone at Cupertino event",
          published_at="2026-09-01T13:00:00+00:00", description="The company's autumn launch.",
          publisher_hint="reuters.com"),
    ], "BBC", "https://feeds.bbci.co.uk/news/rss.xml", scorer, st, source_type="rss")
    return st


def _titles(st, q, **kw):
    kw.setdefault("terms", True)
    kw.setdefault("sort", "relevance")
    rows, total = st.search_feed_articles(q=q, **kw)
    return total, [r["title"] for r in rows]


def test_index_is_created_populated_and_reported():
    st = _seed()
    assert st.fts_ready and st.index_errors == []
    assert st.search_index_status() == {"ready": True, "indexed": 4, "catalogue": 4}


def test_every_word_required_in_any_order_and_stemmed():
    st = _seed()
    assert _titles(st, "trump apple")[0] == 1 and _titles(st, "apple trump")[0] == 1
    assert _titles(st, "ontario dispatch")[0] == 0                     # a word the row lacks
    assert _titles(st, "resign")[1] == ["Prime minister resigns after confidence vote"]   # porter
    assert _titles(st, "confidence votes")[0] == 1                     # stemmed both ways
    assert _titles(st, "ont*")[0] == 1                                  # prefix
    assert _titles(st, "AND NOT")[0] == 0                               # operators are words


def test_relevance_ranks_the_headline_above_the_snippet_and_matches_publisher_and_category():
    st = _seed()
    total, titles = _titles(st, "apple")
    assert total == 3
    assert titles[0] in ("Apple unveils new iPhone at Cupertino event",
                         "Trump asks Apple to change Lake Ontario name")
    assert titles[-1] == "Senate advances budget package after late-night vote"    # snippet-only match last
    assert _titles(st, "bbc")[1] == ["Trump asks Apple to change Lake Ontario name"]  # publisher column
    assert _titles(st, "reuters apple")[0] == 1                                     # publisher + headline
    _, newest = _titles(st, "apple", sort="newest")
    assert newest[0] == "Apple unveils new iPhone at Cupertino event"


def test_filters_compose_with_term_search():
    st = _seed()
    assert _titles(st, "apple", publisher="Reuters")[0] == 1
    assert _titles(st, "apple", date_to="2026-09-01T10:30:00+00:00")[0] == 1
    assert _titles(st, "vote", include_provisional=False)[0] == 2


def test_the_index_follows_updates_and_deletes_and_rebuilds():
    import ingest
    st = _seed()
    with st.session() as s:
        row = s.get(store_mod.FeedArticle, ingest.canonical_url("https://www.npr.org/3"))
        row.title = "Chancellor resigns after confidence vote"
        s.commit()
    assert _titles(st, "chancellor")[0] == 1 and _titles(st, "prime")[0] == 0
    with st.session() as s:
        s.execute(delete(store_mod.FeedArticle).where(
            store_mod.FeedArticle.canonical_url == ingest.canonical_url("https://www.theguardian.com/us/2")))
        s.commit()
    assert _titles(st, "senate")[0] == 0
    assert st.search_index_status()["indexed"] == 3
    assert st.rebuild_search_index() == 3 and _titles(st, "chancellor")[0] == 1


def test_consumer_path_keeps_the_substring_match_unless_opted_in(monkeypatch):
    st = _seed()
    assert search.search(st, query="trump apple")["total"] == 0          # substring, as before
    assert search.search(st, query="Lake Ontario")["total"] == 1
    assert search.search(st, query="trump apple", debug=True)["termSearch"] is False
    monkeypatch.setenv("RWE_SEARCH_TERMS", "1")
    assert search.search(st, query="trump apple")["total"] == 1
    assert search.search(st, query="trump apple", terms=False)["total"] == 0   # explicit wins
    assert search.search(st, query="trump apple", terms=True, debug=True)["termSearch"] is True


def test_match_expression_is_safe_and_bounded():
    f = store_mod.Store.fts_match_expression
    assert f("Trump's \"apple\" AND ont*") == '"Trump" "s" "apple" "AND" "ont"*'
    assert f("   ") is None and f(None) is None
    assert len(f(" ".join(f"w{i}" for i in range(40))).split(" ")) == 16


def test_story_query_finds_events_by_their_members():
    st = _seed()
    rss_ingest.ingest_entries([
        E(url="https://www.cnn.com/5", title="Apple asked by Trump to rename Lake Ontario",
          published_at="2026-09-01T10:30:00+00:00", publisher_hint="cnn.com"),
    ], None, "https://rss.cnn.com/top", rss_ingest.make_scorer(), st, source_type="rss")
    story_service.clear_cache()
    res = story_service.list_stories(st, query="ontario apple")
    assert res["total"] == 1 and "Ontario" in res["stories"][0]["title"]
    assert story_service.list_stories(st, query="senate")["total"] == 0        # single-source: no story
    assert story_service.list_stories(st, query="zzz nothing")["total"] == 0
    assert story_service.list_stories(st)["total"] == 1                        # no query: unchanged


def test_cli_status_rebuild_and_query(tmp_path, capsys):
    db = f"sqlite:///{tmp_path}/idx.db"
    _seed(db)
    assert search_index.main(["--db", db, "status"]) == 0
    assert '"indexed": 4' in capsys.readouterr().out
    assert search_index.main(["--db", db, "rebuild"]) == 0
    assert search_index.main(["--db", db, "query", "apple trump"]) == 0
    out = capsys.readouterr().out
    assert '"total": 1' in out and "Lake Ontario" in out
