"""The lean backfill (examples/backfill_lean.py).

An article's lean is written into its ``scored`` JSON at ingest and read back from there, so
editing outlet_registry.csv changes nothing about articles already in the catalog. Measured: six
outlets were rated and coverage-gap claims moved 61 -> 62 while the audit went on listing
Dailymail.Com, Winnipegfreepress.Com, Inquirer.Com and Variety.Com as unrated -- all rated an hour
earlier. The registry was right and the catalog had not heard.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import backfill_lean as bl        # noqa: E402
import store as store_mod         # noqa: E402


def _row(url, publisher, scored):
    return {"canonicalUrl": url, "publisher": publisher, "scored": json.dumps(scored)}


def test_a_newly_rated_outlet_is_corrected():
    """Variety was rated -1 after these articles were ingested with no lean at all."""
    rows = [_row("u1", "Variety.Com", {"lean": None, "category": "Entertainment"})]
    assert bl.plan(rows) == [("u1", "Variety.Com", None, -1.0)]


def test_an_already_correct_lean_is_left_alone():
    """Idempotent, so it is safe on a schedule or after every curation pass."""
    assert bl.plan([_row("u1", "Variety.Com", {"lean": -1.0})]) == []


def test_an_unrated_registry_row_never_becomes_centre():
    """Brisbane Times has a row for identity and locality but no rating, because MBFC has none.
    Writing 0.0 here would manufacture the exact claim the blank exists to withhold (L2.2)."""
    assert bl.plan([_row("u1", "Brisbanetimes.Com.Au", {"lean": None})]) == []


def test_an_unknown_outlet_is_skipped():
    assert bl.plan([_row("u1", "Some Local Gazette", {"lean": None})]) == []


def test_a_stale_lean_is_updated_not_only_a_missing_one():
    """A rating can change. The test is disagreement with the registry, not absence."""
    plan = bl.plan([_row("u1", "Variety.Com", {"lean": 2.0})])
    assert plan == [("u1", "Variety.Com", 2.0, -1.0)]


def test_only_the_lean_field_is_rewritten():
    """category, register, emotion and confidence were measured per article; the lean is a property
    of the outlet and is the only field the registry owns."""
    st = store_mod.Store("sqlite://")
    scored = {"article_id": "u1", "outlet": "Variety", "category": "Entertainment",
              "lean": None, "selective": 0.42, "register": "reporting"}
    st.upsert_feed_article(canonical_url="u1", url="u1", publisher="Variety.Com",
                           source_publisher="Variety.Com", title="t", description="d", body=None,
                           published_at="2026-07-28T09:00:00+00:00", source_feed="f", scored=scored)
    assert st.apply_lean_backfill([("u1", -1.0)]) == 1
    after = json.loads(st.all_feed_articles_for_lean_backfill()[0]["scored"])
    assert after["lean"] == -1.0
    for k in ("article_id", "outlet", "category", "selective", "register"):
        assert after[k] == scored[k], k


def test_the_backfill_is_idempotent_end_to_end():
    st = store_mod.Store("sqlite://")
    st.upsert_feed_article(canonical_url="u1", url="u1", publisher="Variety.Com",
                           source_publisher="Variety.Com", title="t", description="d", body=None,
                           published_at="2026-07-28T09:00:00+00:00", source_feed="f",
                           scored={"article_id": "u1", "outlet": "Variety", "lean": None})
    rows = st.all_feed_articles_for_lean_backfill()
    st.apply_lean_backfill([(u, new) for u, _, _, new in bl.plan(rows)])
    assert bl.plan(st.all_feed_articles_for_lean_backfill()) == [], "second run has nothing to do"


def test_malformed_scored_json_is_skipped_not_crashed():
    assert bl.plan([{"canonicalUrl": "u1", "publisher": "Variety.Com", "scored": "not json"}]) == []
