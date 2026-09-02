"""Stage 0.3 — rule-extracted entity spans (``entity_spans`` + the store's kind boundary +
``story_service.entity_spans``).

The channel's rules are adopted and measured; its COVERAGE is the gap (24% of articles carry a
provider-extracted name, so X5c is silent on 94% of merges). These pin the extractor's shape, and
— load-bearing — the boundary that keeps production byte-identical while the table fills: span
rows are written under their own kind and source, the store returns them only to a caller that
names the kind, and the build asks for it only under ``RWE_STORY_ENTITY_SPANS=1``.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "examples"))

import audit_clustering_change as acc   # noqa: E402
import entity_span_backfill as backfill # noqa: E402
import entity_spans as es               # noqa: E402
import evidence_resolver as er          # noqa: E402
import rss_ingest                       # noqa: E402
import store as store_mod               # noqa: E402
import story_service as ss              # noqa: E402

T0 = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)
X = es.extract


# --------------------------------------------------------------------------- #
# The extractor.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title, dek, expected", [
    ("Donald Trump meets Vladimir Putin in Helsinki", "", ["donald trump", "vladimir putin"]),
    ("Why Donald Trump is losing the Senate Republicans", "",
     ["donald trump", "senate republicans"]),
    ("Bank of England holds rates as Andrew Bailey warns on inflation", "",
     ["bank of england", "andrew bailey"]),
    ("Breaking: New York Times sues OpenAI over training data", "", ["new york times"]),
    ("Luigi Mangione's lawyers ask judge to toss charges", "", ["luigi mangione"]),
    ("Senate passes the budget", "The bill now goes to President Maria Lopez, who has promised "
     "a veto. Speaker Tom Reed called it dead on arrival. Later sentences are ignored by Ted Cruz.",
     ["president maria lopez", "speaker tom reed"]),
    ("Museo del Prado reopens after strike", "", ["museo del prado"]),
])
def test_capitalised_multi_word_spans_are_names(title, dek, expected):
    assert X(title, dek) == expected


@pytest.mark.parametrize("title, dek", [
    ("Senate Passes The Budget After Late Night Vote", ""),        # Title Case: no signal
    ("BREAKING: MAN ARRESTED AFTER CHASE", ""),
    ("Trump says tariffs will rise", ""),                          # single capitalised words
    ("Storm hits coast", "Thousands lost power overnight."),
    ("대통령이 새로운 예산안을 발표했다", ""),                    # caseless script
    ("", ""),
])
def test_nothing_is_invented_where_capitalisation_carries_no_signal(title, dek):
    assert X(title, dek) == []


def test_title_case_headline_still_yields_from_the_dek():
    assert X("Senate Passes The Budget After Late Night Vote",
             "The measure was drafted by Senator Jane Doe.") == ["senator jane doe"]


def test_noun_capitalising_languages_are_skipped():
    assert X("Bundeskanzler Merz kündigt Neue Regeln an", "", language="de") == []
    assert X("Bundeskanzler Merz kündigt Neue Regeln an", "", language="en") != []


def test_a_comma_ends_a_run_so_a_cast_list_is_several_names():
    """From the first production backfill: 'Julia Stiles, Jenna Dewan, Harry Shum Jr' came back
    as ONE 26-character span, which can never corroborate and only pads the count."""
    assert X("‘Dancing With the Stars’ Season 35 cast revealed: Julia Stiles, Jenna Dewan, "
             "Harry Shum Jr.", "") == ["dancing with the stars", "julia stiles", "jenna dewan",
                                       "harry shum jr"]


def test_calendar_and_format_words_cannot_begin_or_end_a_name():
    """'tuesday sept' was extracted from a premiere dateline on the first backfill — a string
    that would corroborate ACROSS unrelated stories, the one failure the consumers cannot
    absorb. Calendar words are trimmed from a name's ends; a run of nothing else vanishes."""
    assert X("Premiere set for Tuesday Sept 2 after the finale", "") == []
    assert X("Monday Night Football returns with Jane Doe on the call", "") == \
        ["night football", "jane doe"]
    assert X("Storm hits coast", "Live Updates from Mayor Ana Lopez continued.") == \
        ["mayor ana lopez"]


def test_and_is_not_a_connector_and_furniture_is_dropped():
    assert X("Trump and Putin to meet", "") == []
    assert X("Live Updates: Hurricane Elsa nears Florida Keys", "") == \
        ["hurricane elsa", "florida keys"]
    assert X("Live Updates: Hurricane Season begins", "") == [], \
        "'season' is a format word: trimmed, and the lone 'hurricane' left is not a name"
    assert "breaking news" not in X("Breaking News: Storm Elsa nears Florida Keys", "")


def test_names_are_normalised_deduped_and_capped():
    assert X("Pope Leo XIV's first trip: Pope Leo XIV in Lampedusa", "") == ["pope leo xiv"]
    long_dek = " met ".join(f"Alpha{i} Beta{i}" for i in range(40)) + "."
    assert len(X("Storm hits coast", long_dek)) == es.CAP


def test_the_ingest_switch_defaults_off_and_junk_is_off(monkeypatch):
    monkeypatch.delenv("RWE_INGEST_ENTITY_SPANS", raising=False)
    assert es.enabled() is False
    monkeypatch.setenv("RWE_INGEST_ENTITY_SPANS", "garbage")
    assert es.enabled() is False
    monkeypatch.setenv("RWE_INGEST_ENTITY_SPANS", "1")
    assert es.enabled() is True


# --------------------------------------------------------------------------- #
# The store boundary: kinds, sources, defaults.
# --------------------------------------------------------------------------- #
def test_span_rows_live_beside_provider_rows_and_are_returned_only_on_request():
    st = store_mod.Store("sqlite://")
    st.replace_article_entities("u1", {"person": ["jane doe"], "org": ["acme corp"]})
    st.replace_article_entities("u1", {"span": ["jane doe", "riverside county"]},
                                source=es.SOURCE)
    assert st.entities_for_urls(["u1"]) == {"u1": {"person": ["jane doe"], "org": ["acme corp"]}}, \
        "the default read is the provider kinds — every existing consumer stays byte-identical"
    assert st.entities_for_urls(["u1"], kinds=("person", "org", "span")) == {
        "u1": {"person": ["jane doe"], "org": ["acme corp"],
               "span": ["jane doe", "riverside county"]}}
    # Per-source replace: rewriting the spans never touches the provider's rows, and vice versa.
    st.replace_article_entities("u1", {"span": ["new name here"]}, source=es.SOURCE)
    full = st.entities_for_urls(["u1"], kinds=store_mod.ENTITY_KINDS)["u1"]
    assert full["span"] == ["new name here"] and full["person"] == ["jane doe"]
    st.replace_article_entities("u1", {"person": ["john roe"]})
    full = st.entities_for_urls(["u1"], kinds=store_mod.ENTITY_KINDS)["u1"]
    assert full["person"] == ["john roe"] and full["span"] == ["new name here"]
    assert st.replace_article_entities("u2", {"bogus": ["x"]}) == 0, "unknown kinds are ignored"


def test_coverage_counts_distinct_articles_per_kind_set():
    st = store_mod.Store("sqlite://")
    st.replace_article_entities("u1", {"person": ["jane doe"]})
    st.replace_article_entities("u2", {"span": ["acme corp"]}, source=es.SOURCE)
    st.replace_article_entities("u2", {"span": ["acme corp"]}, source=es.SOURCE)
    urls = ["u1", "u2", "u3"]
    assert st.count_entity_covered(urls) == 1
    assert st.count_entity_covered(urls, kinds=("span",)) == 1
    assert st.count_entity_covered(urls, kinds=store_mod.ENTITY_KINDS) == 2


# --------------------------------------------------------------------------- #
# The build: which kinds it asks for, and that the rules consume what the fetch returned.
# --------------------------------------------------------------------------- #
def test_the_build_fetches_spans_only_under_its_own_switch(monkeypatch):
    monkeypatch.delenv("RWE_STORY_ENTITY_SPANS", raising=False)
    monkeypatch.setenv("RWE_STORY_ENTITY_VETO", "1")
    assert ss.entity_kinds() == ("person", "org")
    monkeypatch.setenv("RWE_STORY_ENTITY_SPANS", "1")
    assert ss.entity_kinds() == ("person", "org", "span")
    asked = {}

    class Spy:
        def entities_for_urls(self, urls, kinds=None):
            asked["kinds"] = kinds
            return {}
    ss._entities_for(Spy(), [{"canonicalUrl": "u1"}])
    assert asked["kinds"] == ("person", "org", "span")
    monkeypatch.setenv("RWE_STORY_ENTITY_SPANS", "junk")
    ss._entities_for(Spy(), [{"canonicalUrl": "u1"}])
    assert asked["kinds"] == ("person", "org"), "junk is off"


def _m(url, headline, publisher="P1", hours=0):
    return {"id": url, "url": url, "canonicalUrl": url, "headline": headline, "description": "",
            "publisher": publisher, "publishedAt": (T0 + timedelta(hours=hours)).isoformat()}


def test_x5c_consumes_span_consensus_when_the_fetch_returned_it():
    """The veto's rule is unchanged — disjoint corroborated consensuses refuse a merge — it just
    has testimony where the provider left silence. Which provenances take part was decided at
    the query: the closure counts every kind in the mapping it was handed."""
    arts = [_m("a1", "Rivals clash in the final"), _m("a2", "Final ends in a draw"),
            _m("b1", "Rivals clash in the final"), _m("b2", "Final ends in a draw")]
    spans = {"a1": {"span": ["riverside rovers"]}, "a2": {"span": ["riverside rovers"]},
             "b1": {"span": ["harbour city fc"]}, "b2": {"span": ["harbour city fc"]}}
    stats = {}
    _, ok = ss._entity_closures(arts, spans, True, stats)
    assert ok([0, 1], [2, 3]) is False and stats["entityMergeVetoed"] == 1
    assert ss._entity_closures(arts, {}, True)[1] is None, "an empty mapping is off"


# --------------------------------------------------------------------------- #
# Ingest hook and backfill.
# --------------------------------------------------------------------------- #
def _entry(url, title, desc=""):
    return rss_ingest.FeedEntry(url=url, title=title, description=desc,
                                published_at=T0.isoformat(), publisher_hint="Example Outlet")


def test_ingest_writes_spans_only_when_switched_on(monkeypatch):
    st = store_mod.Store("sqlite://")
    scorer = rss_ingest.make_scorer()
    monkeypatch.delenv("RWE_INGEST_ENTITY_SPANS", raising=False)
    rss_ingest.ingest_entries([_entry("https://example.com/a", "Donald Trump meets Vladimir Putin")],
                              "Example", "feed://x", scorer, st)
    assert st.count_article_entities() == 0, "off writes nothing"
    monkeypatch.setenv("RWE_INGEST_ENTITY_SPANS", "1")
    stats = rss_ingest.ingest_entries(
        [_entry("https://example.com/b", "Jane Doe sues Acme Corp over data breach")],
        "Example", "feed://x", scorer, st)
    assert stats["entity_spans"] == 1
    got = st.entities_for_urls([er._canon("https://example.com/b")], kinds=("span",))
    assert got == {er._canon("https://example.com/b"): {"span": ["acme corp", "jane doe"]}}, \
        "the store returns names sorted, as it does for provider kinds"
    assert st.entities_for_urls([er._canon("https://example.com/b")]) == {}, \
        "the default read still returns nothing: no consumer sees these without opting in"
    again = rss_ingest.ingest_entries(
        [_entry("https://example.com/b", "Jane Doe sues Acme Corp over data breach")],
        "Example", "feed://x", scorer, st)
    assert "entity_spans" not in again, "a re-poll changes no text and writes no rows"


def test_the_backfill_reports_coverage_before_and_after(capsys):
    st = store_mod.Store("sqlite://")
    rows = [{"canonicalUrl": "u1", "title": "Jane Doe sues Acme Corp", "description": "",
             "language": "en"},
            {"canonicalUrl": "u2", "title": "Storm hits coast", "description": "", "language": "en"},
            {"canonicalUrl": "u3", "title": "Bundeskanzler Merz kündigt Neue Regeln an",
             "description": "", "language": "de"}]
    st.replace_article_entities("u2", {"person": ["someone else"]})
    dry = backfill.run(st, dry_run=True, rows=rows)
    assert dry["articlesWithSpans"] == 1 and dry["rowsWritten"] == 0
    assert st.count_entity_covered(["u1", "u2", "u3"], kinds=("span",)) == 0
    res = backfill.run(st, rows=rows, show=2)
    assert res["coveredBefore"] == 1 and res["coveredAfter"] == 2
    assert res["english"] == 2 and res["englishWithSpans"] == 1
    assert res["byLanguage"]["de"] == (1, 0), "German skipped by language, counted honestly"
    text = backfill.render(res)
    assert "provider-covered      : 1 (33.3%)" in text
    assert "covered with spans    : 2 (66.7%)" in text
    assert "English               : 1 of 2 carry spans (50.0%)" in text
    assert "jane doe, acme corp" in text


def test_the_backfill_closing_line_states_the_read_switch(monkeypatch):
    """After adoption the old line ("nothing reads span rows until…") was false on the box
    that printed it. The note reports the switch's actual state in the running environment."""
    res = backfill.run(store_mod.Store("sqlite://"), rows=[])
    monkeypatch.delenv("RWE_STORY_ENTITY_SPANS", raising=False)
    assert "is OFF here" in backfill.render(res) and "measure first" in backfill.render(res)
    monkeypatch.setenv("RWE_STORY_ENTITY_SPANS", "1")
    text = backfill.render(res)
    assert "is ON here" in text and "-e RWE_STORY_ENTITY_SPANS=0" in text


def test_the_audit_flag_widens_the_after_side_and_reports_an_empty_table(tmp_path, monkeypatch,
                                                                         capsys):
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "0")
    monkeypatch.delenv("RWE_STORY_ENTITY_SPANS", raising=False)
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "100000")
    st = store_mod.Store(f"sqlite:///{tmp_path / 'spans.db'}")
    # Each club's two write-ups are word-identical (Jaccard 1.0) and the cross pairs share all
    # but one token, so best-first linkage forms BOTH two-article clusters before it tries to
    # join them — which is when X5c has a corroborated consensus on each side to compare. (A
    # singleton joining a cluster fails open by design, so four identical headlines would be
    # absorbed one at a time and the veto never consulted with testimony on both sides.)
    titles = {"A": "Rivals clash in cup final at stadium tonight after extra time",
              "B": "Rivals clash in cup final at stadium tonight after extra time",
              "C": "Rivals clash in cup final at stadium tonight after penalties",
              "D": "Rivals clash in cup final at stadium tonight after penalties"}
    for pub, title in titles.items():
        url = f"https://{pub.lower()}.example.com/final"
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title, description="d", body=None, published_at=T0.isoformat(),
            source_feed="f", scored={"article_id": er._canon(url), "outlet": pub,
                                     "category": "Sport", "lean": 0.0, "title": title})
    rc = acc.main(["--db", f"sqlite:///{tmp_path / 'spans.db'}", "--entity-spans"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "entity spans       : 0 of 4 window articles" in out and "TABLE EMPTY" in out
    # Fill the table with two disjoint corroborated span consensuses: with the veto on and the
    # spans consumed, the four-article template weld is refused as two clubs' finals.
    monkeypatch.setenv("RWE_STORY_ENTITY_VETO", "1")
    for pub, name in (("a", "riverside rovers"), ("b", "riverside rovers"),
                      ("c", "harbour city fc"), ("d", "harbour city fc")):
        st.replace_article_entities(er._canon(f"https://{pub}.example.com/final"),
                                    {"span": [name]}, source=es.SOURCE)
    rc = acc.main(["--db", f"sqlite:///{tmp_path / 'spans.db'}", "--entity-spans"])
    out = capsys.readouterr().out
    assert rc == 0
    after = next(l for l in out.splitlines() if l.startswith("after"))
    before = next(l for l in out.splitlines() if l.startswith("before"))
    assert "entity-spans" in after and "entity-spans" not in before
    assert "entity spans       : 4 of 4 window articles" in out
    assert "clusters split     : 1" in out, "the provider-blind weld is severed on span testimony"
