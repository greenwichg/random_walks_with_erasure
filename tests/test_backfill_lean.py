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


# --------------------------------------------------------------------------- #
# Orphaned leans — the direction the backfill could not previously reach.
#
# It writes a lean; it never withdrew one. That stayed invisible until a RESOLUTION fix shipped:
# `The Star (Malaysia)` stopped claiming the bare name `The Star`, and six stored articles kept a
# +2 the registry no longer stood behind — still voting, in production.
# --------------------------------------------------------------------------- #
def test_a_lean_whose_outlet_stopped_resolving_is_orphaned():
    """The production case, in miniature. Nothing about the ARTICLE changed; the registry did."""
    assert bl.is_orphaned("Some Outlet That Resolves To Nothing", 2.0) is True
    assert bl.is_orphaned("Some Outlet That Resolves To Nothing", None) is False


def test_a_lean_whose_outlet_became_unrated_is_orphaned(monkeypatch):
    """The other way an outlet stops asserting a lean: the rating is withdrawn because it turned
    out to be wrong. The row still resolves; it just no longer says anything."""
    import outlet_registry

    class _Unrated:
        lean = float("nan")

    monkeypatch.setattr(outlet_registry, "resolve", lambda n: _Unrated())
    assert bl.is_orphaned("Widget Times", -1.0) is True


def test_a_correctly_rated_article_is_not_orphaned():
    """The guard that keeps this from nulling the whole catalog."""
    assert bl.is_orphaned("reuters.com", 0.0) is False
    assert bl.is_orphaned("foxnews.com", 2.0) is False


def test_garbage_in_the_stored_lean_is_left_alone():
    """Not our field to clean up, and a crash here would take the whole pass down."""
    assert bl.is_orphaned("nothing.example", "banana") is False
    assert bl.is_orphaned("nothing.example", {"x": 1}) is False


def test_plan_orphans_names_the_article_and_what_it_carried():
    rows = [{"canonicalUrl": "u1", "publisher": "Totally Unknown Masthead",
             "scored": '{"lean": 2.0, "category": "Politics"}'},
            {"canonicalUrl": "u2", "publisher": "reuters.com", "scored": '{"lean": 0.0}'}]
    out = bl.plan_orphans(rows)
    assert out == [("u1", "Totally Unknown Masthead", 2.0)]


def test_the_two_plans_never_overlap():
    """`plan` writes a lean, `plan_orphans` removes one — an article in both would mean the registry
    simultaneously does and does not rate its outlet."""
    rows = [{"canonicalUrl": "u1", "publisher": "Totally Unknown Masthead", "scored": '{"lean": 2.0}'},
            {"canonicalUrl": "u2", "publisher": "foxnews.com", "scored": '{"lean": null}'}]
    assert not ({r[0] for r in bl.plan(rows)} & {r[0] for r in bl.plan_orphans(rows)})
