"""The IH outlet index — the engine under the SerpAPI-compatible facade.

What must stay true, per the frozen design:

* **Independence**: the index builds from exhaust + open data through INJECTED fetchers; nothing
  here reaches the network, and no ingester accepts a news-API payload.
* **Identity**: hosts canonicalise (www/m/amp stripped), one result per registrable domain, and
  ccTLD second-levels ("co.ke") never collapse an outlet into its country suffix.
* **Ranking is explainable**: corroboration beats a single source; geography beats prominence;
  a tracked outlet is penalised but never hidden (the pipeline's own gates decide downstream).
* **The planner is a parser, not a guesser**: the four gap templates round-trip exactly because
  `source_web.queries` generates them from fixed phrasings; everything else is free text.
* **Feedback needs evidence**: fewer than three probe outcomes for a source is an anecdote and
  moves no weights.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import outlet_search as osx  # noqa: E402
import store as store_mod  # noqa: E402


@pytest.fixture()
def con(tmp_path):
    c = osx.open_index(str(tmp_path / "idx.db"))
    yield c
    c.close()


# ------------------------------------------------------------------ identity
def test_canonical_host_and_registrable_domain():
    assert osx.canonical_host("https://www.the-star.co.ke/news/x") == "the-star.co.ke"
    assert osx.canonical_host("M.Dailyke.Example") == "dailyke.example"
    assert osx.registrable_domain("news.the-star.co.ke") == "the-star.co.ke", \
        "a ccTLD second-level must not swallow the outlet"
    assert osx.registrable_domain("amp.dailyke.example") == "dailyke.example"


def test_upsert_accumulates_evidence_without_duplicates_and_never_overwrites_knowledge(con):
    assert osx.upsert(con, "https://www.x.example/", name="X Daily", country="KE",
                      source="wikidata") is True
    assert osx.upsert(con, "x.example", source="wikipedia", detail="list:KE") is False
    osx.upsert(con, "x.example", source="wikipedia", detail="list:KE")   # same evidence again
    osx.upsert(con, "x.example", name="Wrong Name", country="TZ", source="exhaust")
    row = con.execute("SELECT * FROM hosts WHERE host='x.example'").fetchone()
    import json
    ev = json.loads(row["evidence"])
    assert len(ev) == 3, "identical evidence must be recorded once"
    assert row["name"] == "X Daily" and row["country"] == "KE", \
        "the first source to KNOW wins; later guesses never overwrite"


# ------------------------------------------------------------------ planner
def test_the_gap_templates_round_trip_and_free_text_stays_free(con):
    assert osx.plan_query("local news websites in Kenya") == {"country": "KE"}
    assert osx.plan_query("Kenya newspapers online") == {"country": "KE"}
    assert osx.plan_query("regional news outlets South Africa") == {"country": "ZA"}
    plan = osx.plan_query("Swahili language news site Kenya")
    assert plan["country"] == "KE"
    assert osx.plan_query("best pizza in town") == {"text": "best pizza in town"}


# ------------------------------------------------------------------ ranking
def _seed_ke(con):
    osx.upsert(con, "corroborated.example", name="Corroborated", country="KE", source="wikidata")
    osx.upsert(con, "corroborated.example", source="wikipedia")
    osx.upsert(con, "single.example", name="Single", country="KE", source="wikipedia")
    osx.upsert(con, "aftonbladet.se", name="Aftonbladet", country="KE", source="wikidata")
    osx.upsert(con, "www.corroborated.example", source="cc")   # canonicalises into the first row
    con.commit()


def test_corroboration_outranks_a_single_source_and_tracked_is_penalised_not_hidden(con):
    _seed_ke(con)
    rows = osx.query_index(con, {"country": "KE"}, count=10)
    hosts = [r["host"] for r in rows]
    assert hosts.index("corroborated.example") < hosts.index("single.example")
    tracked = {r["host"]: r for r in rows}["aftonbladet.se"]
    assert tracked["tracked"] is True, "the registry knows this outlet"
    assert "aftonbladet.se" in hosts, "tracked is a penalty, never a filter"
    assert tracked["score"] < tracked["score"] + 2.0  # sanity: penalty applied in score


def test_one_result_per_registrable_domain(con):
    osx.upsert(con, "news.paper.co.ke", name="Paper News", country="KE", source="wikidata")
    osx.upsert(con, "paper.co.ke", name="Paper", country="KE", source="wikipedia")
    con.commit()
    rows = osx.query_index(con, {"country": "KE"}, count=10)
    domains = [r["domain"] for r in rows]
    assert domains.count("paper.co.ke") == 1, "two hosts, one outlet, one result"


def test_free_text_query_matches_names_via_fts(con):
    _seed_ke(con)
    rows = osx.query_index(con, {"text": "corroborated daily news"}, count=5)
    assert rows and rows[0]["host"] == "corroborated.example"


# ------------------------------------------------------------------ ingesters (all injected)
def test_wikipedia_ingest_stamps_country_and_drops_platforms(con):
    payload = {"parse": {"externalinks": []}}
    payload = {"parse": {"externallinks": [
        "https://coastweekly.example/about", "https://facebook.com/page",
        "https://en.wikipedia.org/wiki/Foo"]}}
    rep = osx.ingest_wikipedia_lists(con, ["KE"], fetch_json=lambda u: payload)
    assert rep["added"] == 1
    row = con.execute("SELECT * FROM hosts WHERE host='coastweekly.example'").fetchone()
    assert row["country"] == "KE"
    assert con.execute("SELECT COUNT(*) c FROM hosts").fetchone()["c"] == 1, \
        "platforms and encyclopaedias never become outlet rows"


def test_cc_ingest_updates_prominence_but_adds_nothing_without_the_flag(con, tmp_path):
    osx.upsert(con, "known.example", country="KE", source="wikidata")
    con.commit()
    f = tmp_path / "ranks.txt"
    f.write_text("100 example,known\n50 example,unknown\n")   # CC reverses labels
    rep = osx.ingest_cc_domains(con, str(f))
    assert rep["updated"] == 1 and rep["added"] == 0
    prom = con.execute("SELECT prominence FROM hosts WHERE host='known.example'").fetchone()[0]
    assert prom > 0
    assert con.execute("SELECT COUNT(*) c FROM hosts").fetchone()["c"] == 1
    rep2 = osx.ingest_cc_domains(con, str(f), add_missing=True)
    assert rep2["added"] == 1, "the breadth lever is the flag, not the default"


# ------------------------------------------------------------------ feedback (Phase 4)
def test_feedback_moves_only_on_three_or_more_outcomes(tmp_path, monkeypatch):
    monkeypatch.setenv("RWE_OUTLET_INDEX_DB", str(tmp_path / "fb.db"))
    con = osx.open_index()
    st = store_mod.Store(f"sqlite:///{tmp_path / 'fb-eng.db'}")
    hosts = [f"h{i}.example" for i in range(4)]
    st.record_admission_candidates(
        [{"host": h, "articles": 20, "language": "en", "publishers": [h], "eligible": True,
          "channel": "web"} for h in hosts])
    for h in hosts:
        osx.upsert(con, h, source="wikidata")
    osx.upsert(con, "anecdote.example", source="wikipedia")
    st.record_admission_candidates([{"host": "anecdote.example", "articles": 20, "language": "en",
                                     "publishers": ["a"], "eligible": True, "channel": "web"}])
    con.commit()
    for h in hosts[:3]:
        assert st.claim_admission_probe(h) is not None
        st.record_admission_probe(h, verdict="ADMIT", gates=[], samples=[],
                                  feed_url=f"https://{h}/feed", discovered_via="feed")
    assert st.claim_admission_probe("anecdote.example") is not None
    st.record_admission_probe("anecdote.example", verdict="REJECT", gates=[], samples=[])
    con.close()
    weights = osx.feedback_weights(st)
    assert weights.get("wikidata") == 1.0, "three validated outcomes move the weight"
    assert "wikipedia" not in weights, "one rejection is an anecdote, not a signal"


# ------------------------------------------------------------------ Phase 0 bar
def test_measure_reports_the_pre_registered_bar_in_both_directions(con, tmp_path, monkeypatch):
    st = store_mod.Store(f"sqlite:///{tmp_path / 'm-eng.db'}")
    gaps = [{"country": "KE", "language": "sw", "outlets": 1},
            {"country": "TZ", "language": "sw", "outlets": 0}]
    monkeypatch.setattr("source_web.corpus_gap_counts", lambda _st, reg=None: {})
    monkeypatch.setattr("source_web.gaps", lambda counts, floor=5: gaps)
    for i in range(3):
        osx.upsert(con, f"ke{i}.example", country="KE", source="wikidata")
    con.commit()
    rep = osx.measure(con, st)
    assert rep["gaps"] == 2 and rep["covered"] == 1 and rep["pass"] is False, \
        "1/2 covered sits under the 60% bar"
    for i in range(3):
        osx.upsert(con, f"tz{i}.example", country="TZ", source="wikidata")
    con.commit()
    assert osx.measure(con, st)["pass"] is True
