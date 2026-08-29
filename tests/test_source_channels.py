"""Phase 2 acquisition channels — directory and web, and the shared pipeline they meet in.

Three properties are load-bearing and the rest is detail:

* **the web channel cannot reach the network**, because `source_web.discover` has no default search
  callable. That is the ToS review's attachment point, and a default would put the whole question
  behind somebody remembering a flag — the reasoning `source_validation` already carries;
* **a zero-article host can become a candidate**, which the catalogue channel's volume floor
  silently forbade. Every directory and web host has zero articles by construction;
* **provenance survives**, because "which channel yielded how many outlets per request" is the only
  question that decides where a portfolio invests, and no single channel supplies 45,000.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import outlet_registry            # noqa: E402
import source_campaign as sc      # noqa: E402
import source_directory as sdir   # noqa: E402
import source_discovery as sd     # noqa: E402
import source_web as sweb         # noqa: E402
import store as store_mod         # noqa: E402


@pytest.fixture()
def reg():
    return outlet_registry.default_registry()


@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path / 'chan.db'}")


# --------------------------------------------------------------------------- the directory channel
CSV = """name,website,country,language
Mail & Guardian,https://WWW.MG.co.za/news,ZA,en
Daily Maverick,dailymaverick.co.za,ZA,en
Mail and Guardian (dup),http://mg.co.za,ZA,
Not A Site,Some Publication Name,ZA,en
Kathimerini,https://www.kathimerini.gr,GR,el-GR
Long Country,longcountry.example,Deutschland,de
"""


def test_a_register_becomes_evidence_with_country_and_language():
    """The channel's whole advantage: it brings the axis the catalogue channel cannot supply.
    Gate 6 reports UNKNOWN for a catalogue candidate's language; a register states it."""
    recs = {r["host"]: r for r in sdir.parse(CSV, source_ref="za.csv")}
    assert set(recs) == {"mg.co.za", "dailymaverick.co.za", "kathimerini.gr",
                         "longcountry.example"}, "a display name was treated as a host, or a dup survived"
    assert recs["mg.co.za"]["country"] == "ZA"
    assert recs["kathimerini.gr"]["language"] == "el", "a region subtag must be dropped: el-GR -> el"
    assert recs["longcountry.example"]["country"] == "", \
        "a long-form country name must be dropped, not truncated to two letters"
    assert recs["mg.co.za"]["articles"] == 0
    assert recs["mg.co.za"]["directoryRef"] == "za.csv"


def test_the_same_outlet_written_three_ways_is_one_record():
    """`_host_of` strips scheme, www., case and the trailing dot, so canonicalisation happens once
    and every channel inherits it. The first row's evidence wins; a later blank cannot erase it."""
    recs = {r["host"]: r for r in sdir.parse(CSV)}
    mg = recs["mg.co.za"]
    assert mg["language"] == "en", "a later row with a blank language erased an earlier one"
    assert len(mg["publishers"]) == 2, "both spellings of the name should be kept"


def test_a_bare_list_of_domains_parses_without_a_header():
    """Half of what is publicly available is a pasted list. Requiring a header would mean
    hand-editing every register before it could be imported."""
    recs = sdir.parse("# South Africa\nmg.co.za\nhttps://ewn.co.za/\n\nnot a host line\n")
    assert {r["host"] for r in recs} == {"mg.co.za", "ewn.co.za"}


def test_the_directory_channel_admits_a_host_with_no_articles(reg):
    """The catalogue channel's `VOLUME_FLOOR` would reject every directory host — they all have zero
    articles — and `record_admission_candidates` skips anything not eligible, so the whole channel
    would vanish silently. This is the per-channel-eligibility change, asserted."""
    recs = sdir.parse(CSV)
    floored = sd.gate(recs, reg, admissible=sd.volume_floor(10), channel="directory")
    assert not any(c["eligible"] for c in floored), "fixture no longer exercises the floor"

    admitted = sd.gate(recs, reg, admissible=sd.always_admissible, channel="directory")
    assert [c["host"] for c in admitted if c["eligible"]], \
        "a zero-article host cannot become a candidate — the channel is unreachable"
    assert all(c["channel"] == "directory" for c in admitted)


def test_the_shared_gates_still_run_for_a_directory_host(reg):
    """A looser admissibility bar is not a looser gate. Aggregators and already-tracked outlets are
    rejected whatever channel offered them."""
    recs = sdir.parse("news.google.com\nnpr.org\nbrandnewoutlet.example\n")
    cands = {c["host"]: c for c in sd.gate(recs, reg, admissible=sd.always_admissible,
                                           channel="directory")}
    assert cands["news.google.com"]["proxy"] and not cands["news.google.com"]["eligible"]
    assert cands["npr.org"]["tracked"] and not cands["npr.org"]["eligible"]
    assert cands["brandnewoutlet.example"]["eligible"]


# --------------------------------------------------------------------------- the web channel
def test_the_web_channel_cannot_reach_the_network_without_a_fetcher():
    """THE safety property. `discover` has no default search callable, so an unreviewed deployment
    cannot query anything — it plans and stops. A default disabled by a flag would put the ToS
    question behind somebody remembering the flag."""
    plan = sweb.discover([{"country": "ZA", "language": "en", "outlets": 1}])
    assert plan["offline"] is True
    assert plan["records"] == []
    assert plan["searched"] == 0
    assert plan["queries"], "an offline run must still report what it WOULD have asked"


def test_a_supplied_search_is_the_only_way_hosts_appear():
    asked = []

    def _search(q):
        asked.append(q)
        return [{"url": "https://mg.co.za/article/1", "title": "Mail & Guardian"},
                {"url": "https://en.wikipedia.org/wiki/List", "title": "List of newspapers"},
                {"url": "https://news.google.com/rss/x", "title": "Google News"},
                {"url": "", "title": "no url"}]

    plan = sweb.discover([{"country": "ZA", "language": "en", "outlets": 1}],
                         search=_search, per_gap=2)
    assert plan["offline"] is False and asked
    hosts = {r["host"] for r in plan["records"]}
    assert hosts == {"mg.co.za"}, \
        "an encyclopaedia, an aggregator or an empty URL reached the candidate set"
    rec = plan["records"][0]
    assert rec["country"] == "ZA" and rec["language"] == "en"
    assert rec["gap"]["country"] == "ZA", "a candidate must say which gap produced it"


def test_one_failing_query_does_not_end_the_run():
    calls = {"n": 0}

    def _search(q):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("rate limited")
        return [{"url": "https://ewn.co.za/x", "title": "EWN"}]

    plan = sweb.discover([{"country": "ZA", "language": "en", "outlets": 0}],
                         search=_search, per_gap=3)
    assert {r["host"] for r in plan["records"]} == {"ewn.co.za"}
    assert any("error" in q for q in plan["queries"]), "the failure must be reported, not swallowed"


def test_the_run_is_bounded():
    """This channel costs a search request per query plus three probe requests per surviving host.
    An unbounded first run is what the politeness ceiling exists to prevent."""
    def _search(q):
        return [{"url": f"https://out{i}.example/a", "title": f"O{i}"} for i in range(50)]

    plan = sweb.discover([{"country": "ZA", "language": "en", "outlets": 0}],
                         search=_search, per_gap=1, max_hosts=5)
    assert len(plan["records"]) == 5


def test_gaps_are_thinnest_first_and_exclude_covered_pairs():
    found = sweb.gaps({("ZA", "en"): 40, ("GR", "el"): 2, ("NG", "en"): 0}, floor=5)
    assert [(g["country"], g["outlets"]) for g in found] == [("NG", 0), ("GR", 2)]


def test_a_query_naming_a_language_is_skipped_when_the_gap_has_none():
    qs = sweb.queries({"country": "ZA", "language": ""})
    assert qs and not any("{language}" in q or " language news" in q for q in qs)


# --------------------------------------------------------------------------- provenance
def test_the_channel_that_found_a_host_is_recorded_and_never_overwritten(st, reg):
    """"Which channel yielded how many outlets per request" is the only question that decides where
    a portfolio invests. Dedup is by host, so a host three channels find is one row — and the answer
    belongs to the channel that got there first. A later pass overwriting it would make a cheap
    channel re-offering a known host look like an acquisition."""
    first = sd.gate(sdir.parse("brandnewoutlet.example\n"), reg,
                    admissible=sd.always_admissible, channel="directory")
    st.record_admission_candidates(first)
    assert st.admission_row("brandnewoutlet.example")["channel"] == "directory"

    again = sd.gate([{"host": "brandnewoutlet.example", "articles": 99}], reg,
                    admissible=sd.always_admissible, channel="web")
    st.record_admission_candidates(again)
    row = st.admission_row("brandnewoutlet.example")
    assert row["channel"] == "directory", "a second channel overwrote the first finder"
    assert row["articles"] == 99, "evidence should still refresh — only the channel is sticky"


def test_channel_yield_reports_unrecorded_rows_separately(st, reg):
    """Rows seeded before the column existed are NULL. Folding them into `catalogue` would be a
    plausible guess, and it would make the first channel comparison a fiction."""
    st.record_admission_candidates(sd.gate(sdir.parse("a.example\n"), reg,
                                           admissible=sd.always_admissible, channel="directory"))
    st.record_admission_candidates([{"host": "legacy.example", "articles": 50, "eligible": True}])

    by = {r["channel"]: r for r in st.admission_channel_yield()}
    assert by["directory"]["total"] == 1
    assert by["(unrecorded)"]["total"] == 1
    assert by["directory"]["requestsPerAdmitted"] is None, \
        "a ratio with no admissions is unmeasured, not free"


# --------------------------------------------------------------------------- the CLI
def _run(db, *argv):
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sc.main([*argv, "--db", db])
    return out.getvalue()


def test_seed_defaults_to_the_catalogue_channel(tmp_path):
    db = f"sqlite:///{tmp_path / 'c.db'}"
    out = _run(db, "seed")
    assert "channel            : catalogue" in out


def test_seed_imports_a_register_and_records_the_channel(tmp_path):
    reg_file = tmp_path / "za.csv"
    reg_file.write_text(CSV, encoding="utf-8")
    db = f"sqlite:///{tmp_path / 'c.db'}"
    out = _run(db, "seed", "--channel", "directory", "--file", str(reg_file))
    assert "channel            : directory" in out
    assert "with a country" in out

    st = store_mod.Store(db)
    rows = st.admission_rows(states=["candidate"])
    assert rows, "the register produced no candidates"
    assert all(r["channel"] == "directory" for r in rows)
    assert all(r["articles"] == 0 for r in rows), "directory hosts carry no article evidence"


def test_seed_on_the_web_channel_makes_no_request_and_says_so(tmp_path):
    db = f"sqlite:///{tmp_path / 'c.db'}"
    out = _run(db, "seed", "--channel", "web")
    assert "NO SEARCH WAS MADE" in out
    assert "searches made    : 0" in out
    assert store_mod.Store(db).admission_census().get("total", 0) == 0


def test_an_unknown_channel_is_refused(tmp_path):
    import contextlib
    import io
    db = f"sqlite:///{tmp_path / 'c.db'}"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = sc.main(["seed", "--channel", "nope", "--db", db])
    assert rc == 2 and "unknown channel" in out.getvalue()


# --------------------------------------------------------------------------- the search adapter
@pytest.fixture(autouse=True)
def _clean_search_env(monkeypatch):
    for k in ("RWE_WEB_SEARCH_PROVIDER", "RWE_WEB_SEARCH_API_KEY", "RWE_WEB_SEARCH_CX",
              "RWE_WEB_SEARCH_ENDPOINT", "RWE_WEB_SEARCH_RESULTS", "RWE_WEB_SEARCH_URL_FIELD",
              "RWE_WEB_SEARCH_TITLE_FIELD", "RWE_WEB_SEARCH_COUNT", "RWE_WEB_SEARCH_HEADER"):
        monkeypatch.delenv(k, raising=False)


def test_an_unconfigured_environment_yields_no_adapter():
    """The default that keeps an unreviewed deployment silent. `discover` treats None as
    "plan, do not ask", so no combination of other switches can produce a request."""
    assert sweb.search_adapter() is None
    assert sweb.search_config_warning() is None


def test_a_configured_provider_builds_a_working_search(monkeypatch):
    monkeypatch.setenv("RWE_WEB_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("RWE_WEB_SEARCH_API_KEY", "SECRET")
    seen = {}

    def _get_json(url, headers=None, **kw):
        seen["url"], seen["headers"] = url, headers or {}
        return {"web": {"results": [{"url": "https://mg.co.za/a", "title": "M&G"},
                                    {"url": "https://ewn.co.za/b", "title": "EWN"}]}}

    search = sweb.search_adapter(get_json=_get_json)
    assert search is not None
    out = search("local news websites in South Africa")
    assert out == [{"url": "https://mg.co.za/a", "title": "M&G"},
                   {"url": "https://ewn.co.za/b", "title": "EWN"}]
    assert "local+news+websites+in+South+Africa" in seen["url"], "the query must be URL-encoded"
    assert seen["headers"]["X-Subscription-Token"] == "SECRET"


def test_each_preset_reads_its_own_payload_shape(monkeypatch):
    """A provider preset that pointed at the wrong JSON path would return zero results and read as
    'the web has no outlets for this gap'."""
    payloads = {
        "brave": {"web": {"results": [{"url": "https://a.example", "title": "A"}]}},
        "google_cse": {"items": [{"link": "https://a.example", "title": "A"}]},
        "serpapi": {"organic_results": [{"link": "https://a.example", "title": "A"}]},
    }
    for name, payload in payloads.items():
        monkeypatch.setenv("RWE_WEB_SEARCH_PROVIDER", name)
        monkeypatch.setenv("RWE_WEB_SEARCH_API_KEY", "K")
        monkeypatch.setenv("RWE_WEB_SEARCH_CX", "CX")
        search = sweb.search_adapter(get_json=lambda url, headers=None, **kw: payload)
        assert search("q") == [{"url": "https://a.example", "title": "A"}], f"{name} preset"


def test_a_half_configured_provider_is_a_misconfiguration_not_an_empty_result(monkeypatch):
    monkeypatch.setenv("RWE_WEB_SEARCH_PROVIDER", "brave")
    assert "API_KEY" in (sweb.search_config_warning() or "")

    monkeypatch.setenv("RWE_WEB_SEARCH_API_KEY", "K")
    monkeypatch.setenv("RWE_WEB_SEARCH_PROVIDER", "google_cse")
    assert "RWE_WEB_SEARCH_CX" in (sweb.search_config_warning() or "")

    monkeypatch.setenv("RWE_WEB_SEARCH_PROVIDER", "notaprovider")
    assert "not one of" in (sweb.search_config_warning() or "")


def test_a_malformed_payload_yields_no_hosts_rather_than_raising():
    assert sweb._dig({"web": "not a list"}, "web.results") == []
    assert sweb._dig(None, "a.b") == []
    assert sweb._dig({"items": [1, 2]}, "items") == [1, 2]
    # The case that matters: the path RESOLVES, to something that is not a list. Returning it
    # unchanged would have the caller iterate a string character by character and build a candidate
    # host out of every letter.
    assert sweb._dig({"web": {"results": "oops"}}, "web.results") == []
    assert sweb._dig({"web": {"results": {"a": 1}}}, "web.results") == []


def test_seed_web_asks_nobody_unless_search_is_passed(tmp_path, monkeypatch):
    """Configuring a provider is not the same as authorising a run. An operator who set the env
    vars last week must still be able to PLAN a campaign without querying anyone."""
    monkeypatch.setenv("RWE_WEB_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("RWE_WEB_SEARCH_API_KEY", "K")
    calls = []
    monkeypatch.setattr(sweb, "search_adapter",
                        lambda **kw: (lambda q: calls.append(q) or []))
    db = f"sqlite:///{tmp_path / 'c.db'}"
    out = _run(db, "seed", "--channel", "web")
    assert calls == [], "the channel searched without --search"
    assert "Pass --search to actually query" in out
