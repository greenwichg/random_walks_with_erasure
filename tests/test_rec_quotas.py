"""Story / topic feed quotas in ``Backend._select_diverse`` — Tier 1 of the X-audit roadmap
(docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md).

The selector's three existing invariants must survive the new quotas verbatim: per-strategy
budgets are preserved exactly, the feed never shrinks (declined candidates spill and top back
up), and rank order is never promoted — a quota only ever SKIPS. On top of those: a story caps
like a publisher does, a topic caps like a publisher does, both default OFF (env unset ⇒ the
historical selection, byte-identical), and the explain observer's call site carries the same
quota inputs as the serving one so an enabled cap cannot make the two describe different feeds.
"""

import pathlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import api_server  # noqa: E402

S = api_server.Backend._select_diverse


def _pub_of(pubs):
    return lambda c: pubs[c]


def test_caps_off_is_the_historical_selection(monkeypatch):
    monkeypatch.delenv("RWE_REC_MAX_PER_STORY", raising=False)
    monkeypatch.delenv("RWE_REC_MAX_PER_TOPIC", raising=False)
    pubs = ["P1", "P1", "P2", "P3"]
    stories = {0: "s1", 1: "s1", 2: "s1", 3: "s1"}     # all one story — and it must not matter
    picks = S([("a", [0, 1, 2, 3])], [("a", 4)], _pub_of(pubs), cap=0,
              story_of=stories.get, topic_of=lambda c: "t")
    assert picks == [(0, "a"), (1, "a"), (2, "a"), (3, "a")]


def test_story_cap_dedups_a_story_and_backfills(monkeypatch):
    monkeypatch.setenv("RWE_REC_MAX_PER_STORY", "1")
    pubs = ["P1", "P2", "P3", "P4"]
    stories = {0: "s1", 1: "s1", 2: "s1"}              # 3 takes on one story; col 3 storyless
    picks = S([("a", [0, 1, 2, 3])], [("a", 2)], _pub_of(pubs), cap=0,
              story_of=stories.get, topic_of=None)
    # One card for story s1 (the highest-ranked take), then the storyless article — the second
    # and third takes were skipped, not the budget.
    assert picks == [(0, "a"), (3, "a")]


def test_story_cap_never_shrinks_the_feed(monkeypatch):
    monkeypatch.setenv("RWE_REC_MAX_PER_STORY", "1")
    pubs = ["P1", "P2", "P3"]
    stories = {0: "s1", 1: "s1", 2: "s1"}              # nothing BUT the story
    picks = S([("a", [0, 1, 2])], [("a", 3)], _pub_of(pubs), cap=0,
              story_of=stories.get, topic_of=None)
    # The spill tops the slice back up: a thin catalog serves the old feed, not a short one.
    assert picks == [(0, "a"), (1, "a"), (2, "a")]


def test_topic_cap_bounds_a_monoculture(monkeypatch):
    monkeypatch.setenv("RWE_REC_MAX_PER_TOPIC", "2")
    pubs = ["P1", "P2", "P3", "P4", "P5"]
    topics = ["politics", "politics", "politics", "culture", "politics"]
    picks = S([("a", [0, 1, 2, 3, 4])], [("a", 4)], _pub_of(pubs), cap=0,
              story_of=None, topic_of=lambda c: topics[c])
    # Two politics cards, then culture jumps the queue past the third politics take; the budget
    # still fills to 4 from the spill.
    assert picks == [(0, "a"), (1, "a"), (3, "a"), (2, "a")]


def test_quotas_compose_with_the_publisher_cap(monkeypatch):
    monkeypatch.setenv("RWE_REC_MAX_PER_STORY", "1")
    pubs = ["P1", "P1", "P2", "P3"]
    stories = {2: "s1", 3: "s1"}
    picks = S([("a", [0, 1, 2, 3])], [("a", 3)], _pub_of(pubs), cap=1,
              story_of=stories.get, topic_of=None)
    # P1 caps after col 0; story s1 caps after col 2; col 1 and col 3 spill; top-up restores
    # the budget in rank order.
    assert picks == [(0, "a"), (2, "a"), (1, "a")]


def test_budgets_are_preserved_across_strategies(monkeypatch):
    monkeypatch.setenv("RWE_REC_MAX_PER_STORY", "1")
    pubs = ["P1", "P2", "P3", "P4"]
    stories = {0: "s1", 2: "s1"}
    picks = S([("a", [0, 1]), ("b", [2, 3])], [("a", 2), ("b", 2)], _pub_of(pubs), cap=0,
              story_of=stories.get, topic_of=None)
    # Strategy b's first pick (col 2) hits the story cap and spills, but b still contributes
    # its full budget of 2 — the openness slider's slice budgets keep meaning what they meant.
    assert [s for _, s in picks] == ["a", "a", "b", "b"]
    assert picks == [(0, "a"), (1, "a"), (3, "b"), (2, "b")]


def test_story_by_col_resolves_ids_and_novel_urls():
    be = SimpleNamespace(story_by_id={"Q0": "s1"},
                         story_by_url={"https://ex.com/a": "s2"},
                         _story_col_cache={})
    mind = SimpleNamespace(dataset=SimpleNamespace(
        item_ids=np.asarray(["Q0", "Q1", "https://ex.com/a"], dtype=object)))
    out = api_server.Backend._story_by_col(be, mind)
    assert out == {0: "s1", 2: "s2"}                   # Q1: no story — absent, never guessed
    # Memoized per mind object, exactly like the country map.
    assert api_server.Backend._story_by_col(be, mind) is out


def test_load_story_maps_joins_through_canonicalisation(tmp_path):
    import feed_source
    csv_path = tmp_path / "cat.csv"
    csv_path.write_text("title,text,outlet,category,political_leaning,country,url\n"
                        "T0,x,O,Topic,LEFT,,https://www.ex.com/a?utm_source=rss\n"
                        "T1,x,O,Topic,LEFT,,https://ex.com/b\n", encoding="utf-8")
    import ingest
    canon_a = ingest.canonical_url("https://www.ex.com/a?utm_source=rss")
    store_ = SimpleNamespace(story_member_ids=lambda: {canon_a: "s9"})
    by_id, by_url = feed_source.load_story_maps(store_, str(csv_path))
    assert by_url == {canon_a: "s9"}
    assert by_id == {"Q0": "s9"}                       # joined despite www./utm noise
    assert feed_source.load_story_maps(None, str(csv_path)) == ({}, {})


def test_explain_passes_the_same_quota_inputs_as_serving():
    import ast
    src = (ROOT / "examples" / "rec_explain.py").read_text(encoding="utf-8")
    call = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_select_diverse")
    kw = {k.arg for k in call.keywords}
    assert {"story_of", "topic_of"} <= kw, (
        "rec_explain's _select_diverse call must carry the quota inputs — without them an "
        "enabled story/topic cap makes the explained feed drift from the served one.")
