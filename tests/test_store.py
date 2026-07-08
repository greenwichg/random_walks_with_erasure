"""Unit tests for examples/store.py — the beta persistence layer (SQLite / SQLAlchemy).

Uses an in-memory database (a shared ``StaticPool`` connection) so nothing touches disk;
verifies the identity upsert is idempotent and that users are retrievable by their engine id.
This module is not imported by the JSON API yet, so no other suite is affected.
"""

import importlib.util
import json
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_store():
    spec = importlib.util.spec_from_file_location("store", ROOT / "examples" / "store.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["store"] = mod
    spec.loader.exec_module(mod)
    return mod


store_mod = _load_store()


@pytest.fixture
def store():
    """A fresh in-memory store per test (StaticPool keeps the one connection alive)."""
    return store_mod.Store("sqlite:///:memory:")


def test_upsert_creates_user_and_identity(store):
    user = store.upsert_user_by_identity("google", "acct-1", email="ada@example.com",
                                         display_name="Ada")
    assert user.id is not None
    assert user.email == "ada@example.com"
    assert user.display_name == "Ada"
    # the same row is retrievable by its stable engine id
    again = store.get_user(user.id)
    assert again is not None and again.id == user.id


def test_upsert_is_idempotent(store):
    a = store.upsert_user_by_identity("google", "acct-1", display_name="Ada")
    b = store.upsert_user_by_identity("google", "acct-1", display_name="Ada L.")
    assert a.id == b.id                     # same identity -> same user, not a second one
    assert b.display_name == "Ada L."       # profile refreshed when a new value is supplied


def test_upsert_keeps_profile_when_none(store):
    a = store.upsert_user_by_identity("google", "acct-1", display_name="Ada")
    b = store.upsert_user_by_identity("google", "acct-1")   # no new profile fields
    assert b.id == a.id
    assert b.display_name == "Ada"          # left as-is, not wiped to None


def test_distinct_identities_get_distinct_users(store):
    a = store.upsert_user_by_identity("google", "acct-1")
    b = store.upsert_user_by_identity("google", "acct-2")
    assert a.id != b.id


def test_get_missing_user_is_none(store):
    assert store.get_user(99999) is None


def test_email_and_name_optional(store):
    user = store.upsert_user_by_identity("google", "acct-x")
    assert user.email is None
    assert user.display_name is None


def test_onboarding_upsert_and_get(store):
    u = store.upsert_user_by_identity("google", "ob-1")
    assert store.get_onboarding(u.id) is None
    store.save_onboarding(u.id, ["nyt", "wsj", "ap"])
    assert store.get_onboarding(u.id) == ["nyt", "wsj", "ap"]
    store.save_onboarding(u.id, ["bbc"])                 # upsert replaces
    assert store.get_onboarding(u.id) == ["bbc"]


def test_report_snapshot_latest(store):
    u = store.upsert_user_by_identity("google", "ob-2")
    assert store.latest_report(u.id) is None
    store.save_report(u.id, {"mode": "estimate", "overall": 50, "metrics": []})
    store.save_report(u.id, {"mode": "estimate", "overall": 61, "metrics": []})
    latest = store.latest_report(u.id)
    assert latest is not None and latest["overall"] == 61 and latest["mode"] == "estimate"


def test_scored_article_cache_roundtrip(store):
    assert store.get_scored_article("https://x.com/a") is None
    store.save_scored_article("https://x.com/a", {"outlet": "x.com", "lean": 1.0})
    assert store.get_scored_article("https://x.com/a") == {"outlet": "x.com", "lean": 1.0}
    store.save_scored_article("https://x.com/a", {"outlet": "x.com", "lean": 2.0})   # upsert
    assert store.get_scored_article("https://x.com/a")["lean"] == 2.0


def test_reads_idempotent_and_counted(store):
    u = store.upsert_user_by_identity("google", "reads-store-1")
    assert store.count_reads(u.id) == 0
    assert store.add_read(u.id, "https://x.com/a", {"outlet": "x.com", "lean": 1.0}) is True
    assert store.add_read(u.id, "https://x.com/a", {"outlet": "x.com", "lean": 1.0}) is False  # dup
    assert store.add_read(u.id, "https://y.com/b", {"outlet": "y.com", "lean": -1.0}) is True
    assert store.count_reads(u.id) == 2
    reads = store.get_reads(u.id)
    assert len(reads) == 2 and reads[0]["outlet"] == "x.com"


def test_api_token_mint_resolve_and_revoke(store):
    u = store.upsert_user_by_identity("google", "tok-1")
    token, meta = store.create_token(u.id, label="extension")
    assert token.startswith("ih_") and len(token) > 20
    assert meta["id"] is not None and meta["label"] == "extension" and meta["createdAt"]
    # the token resolves to its user; a bogus / empty token does not
    assert store.resolve_token(token) == u.id
    assert store.resolve_token("ih_not-a-real-token") is None
    assert store.resolve_token("") is None
    # listed metadata never includes the plaintext or the hash; last-used is now populated
    listed = store.list_tokens(u.id)
    assert len(listed) == 1 and listed[0]["id"] == meta["id"]
    assert set(listed[0]) == {"id", "label", "createdAt", "lastUsedAt"}
    assert listed[0]["lastUsedAt"] is not None                 # resolve() touched it
    # revoked tokens stop resolving; revoking someone else's token fails
    other = store.upsert_user_by_identity("google", "tok-2")
    assert store.revoke_token(other.id, meta["id"]) is False
    assert store.revoke_token(u.id, meta["id"]) is True
    assert store.resolve_token(token) is None
    assert store.list_tokens(u.id) == []


def test_api_token_stored_only_as_hash(store):
    """A DB leak must never reveal a usable token — only its SHA-256 hash is persisted."""
    u = store.upsert_user_by_identity("google", "tok-hash")
    token, _ = store.create_token(u.id)
    with store.session() as s:
        row = s.scalar(store_mod.select(store_mod.ApiToken)
                       .where(store_mod.ApiToken.user_id == u.id))
        assert row.token_hash == store_mod.Store._hash_token(token)
        assert token not in row.token_hash and len(row.token_hash) == 64


# --------------------------------------------------------------------------- #
# recommendation reception — the Open-Mindedness feedback loop
# --------------------------------------------------------------------------- #
def test_recommendation_reception_counts_cross_cutting_opens(store):
    u = store.upsert_user_by_identity("google", "rec-1")
    # surface 3 cross-cutting recs + 1 non-cross-cutting (only cross-cutting count for the metric)
    store.record_recommendations_shown(u.id, [("a", True), ("b", True), ("c", True), ("d", False)])
    r = store.recommendation_reception(u.id)
    assert r == {"shownCross": 3, "openedCross": 0, "rate": 0.0}
    # opening two cross-cutting recs raises the reception rate
    assert store.record_recommendation_open(u.id, "a") is True
    assert store.record_recommendation_open(u.id, "b") is True
    r = store.recommendation_reception(u.id)
    assert r["shownCross"] == 3 and r["openedCross"] == 2 and r["rate"] == pytest.approx(2 / 3)
    # a non-cross-cutting open never affects the cross-cutting reception
    store.record_recommendation_open(u.id, "d")
    assert store.recommendation_reception(u.id)["shownCross"] == 3


def test_recommendation_open_is_idempotent(store):
    u = store.upsert_user_by_identity("google", "rec-2")
    store.record_recommendations_shown(u.id, [("x", True)])
    assert store.record_recommendation_open(u.id, "x") is True    # first open counts
    assert store.record_recommendation_open(u.id, "x") is False   # re-open is a no-op
    assert store.recommendation_reception(u.id)["openedCross"] == 1


def test_reshowing_recs_is_idempotent_and_keeps_open(store):
    u = store.upsert_user_by_identity("google", "rec-3")
    store.record_recommendations_shown(u.id, [("a", True), ("b", True)])
    store.record_recommendation_open(u.id, "a")
    # re-surfacing the same recs must not create duplicates nor clear the recorded open
    new = store.record_recommendations_shown(u.id, [("a", True), ("b", True)])
    assert new == 0
    r = store.recommendation_reception(u.id)
    assert r["shownCross"] == 2 and r["openedCross"] == 1


def test_open_before_shown_creates_row(store):
    """An open that arrives before the surfacing was recorded (a race) still counts, using the
    caller's cross_cutting hint."""
    u = store.upsert_user_by_identity("google", "rec-4")
    assert store.record_recommendation_open(u.id, "z", cross_cutting=True) is True
    r = store.recommendation_reception(u.id)
    assert r["shownCross"] == 1 and r["openedCross"] == 1
    assert store.recommendation_reception(u.id)["rate"] == pytest.approx(1.0)


def test_reception_empty_for_new_user(store):
    u = store.upsert_user_by_identity("google", "rec-5")
    assert store.recommendation_reception(u.id) == {"shownCross": 0, "openedCross": 0, "rate": None}


# --------------------------------------------------------------------------- #
# reading-history projection (list_reads) — newest-first, with row metadata
# --------------------------------------------------------------------------- #
def test_list_reads_newest_first_with_metadata(store):
    u = store.upsert_user_by_identity("google", "hist-1")
    store.add_read(u.id, "https://a.com/1", {"article_id": "https://a.com/1", "title": "One"},
                   "2026-06-01T00:00:00Z")
    store.add_read(u.id, "https://a.com/2", {"article_id": "https://a.com/2", "title": "Two"},
                   "2026-06-02T00:00:00Z")
    rows = store.list_reads(u.id)
    assert [r["scored"]["title"] for r in rows] == ["Two", "One"]     # newest (highest id) first
    assert rows[0]["canonicalUrl"] == "https://a.com/2"
    assert rows[0]["observedAt"] == "2026-06-02T00:00:00Z"
    assert isinstance(rows[0]["id"], int) and rows[0]["createdAt"]    # stable id + insert timestamp
    # get_reads is unchanged: oldest-first, scored payloads only (the augmented-corpus input)
    assert [r["title"] for r in store.get_reads(u.id)] == ["One", "Two"]


def test_list_reads_empty_for_new_user(store):
    u = store.upsert_user_by_identity("google", "hist-empty")
    assert store.list_reads(u.id) == []


def test_list_report_snapshots_oldest_first(store):
    u = store.upsert_user_by_identity("google", "snap-1")
    assert store.list_report_snapshots(u.id) == []               # none yet
    store.save_report(u.id, {"mode": "estimate", "overall": 55})
    store.save_report(u.id, {"mode": "measured", "overall": 61})
    snaps = store.list_report_snapshots(u.id)
    assert [s["overall"] for s in snaps] == [55, 61]             # oldest-first (the trend order)
    assert [s["mode"] for s in snaps] == ["estimate", "measured"]
    assert all(s["createdAt"] for s in snaps) and all(isinstance(s["id"], int) for s in snaps)
    assert [s["overall"] for s in store.list_report_snapshots(u.id, limit=1)] == [61]   # keep most recent


def test_report_metric_series_parses_snapshots(store):
    u = store.upsert_user_by_identity("google", "ana-1")
    store.save_report(u.id, {"mode": "measured", "overall": 70,
                             "metrics": [{"key": "topicDiversity", "score": 65},
                                         {"key": "viewpointBalance", "score": 40}],
                             "attention": {"fear": 0.2, "outrage": 0.1, "analysis": 0.4,
                                           "positive": 0.2, "neutral": 0.1}})
    store.save_report(u.id, {"mode": "measured", "overall": 74,
                             "metrics": [{"key": "topicDiversity", "score": 68}],
                             "attention": {"fear": 0.1}})
    series = store.report_metric_series(u.id)
    assert [s["overall"] for s in series] == [70, 74]                # oldest-first
    assert series[0]["metrics"] == {"topicDiversity": 65, "viewpointBalance": 40}
    assert series[0]["attention"]["analysis"] == 0.4
    assert all(len(s["date"]) == 10 for s in series)                # YYYY-MM-DD day
    assert store.report_metric_series(u.id, limit=1)[0]["overall"] == 74
    other = store.upsert_user_by_identity("google", "ana-empty")
    assert store.report_metric_series(other.id) == []               # honest empty


def test_list_rec_events(store):
    u = store.upsert_user_by_identity("google", "ana-recev")
    store.record_recommendations_shown(u.id, [("a", True), ("b", False)])
    store.record_recommendation_open(u.id, "a")
    evs = store.list_rec_events(u.id)
    assert len(evs) == 2
    opened = next(e for e in evs if e["openedAt"])
    assert opened["shownAt"] and opened["crossCutting"] is True
    unopened = next(e for e in evs if not e["openedAt"])
    assert unopened["shownAt"] and unopened["openedAt"] is None


# --------------------------------------------------------------------------- #
# user settings — preference persistence (kept separate from health state)
# --------------------------------------------------------------------------- #
def test_user_settings_roundtrip_and_default(store):
    u = store.upsert_user_by_identity("google", "set-1")
    assert store.get_settings(u.id) is None                      # unconfigured -> None (caller defaults)
    store.save_settings(u.id, {"theme": "dark", "notifications": {"weeklyDigest": False}})
    got = store.get_settings(u.id)
    assert got["theme"] == "dark" and got["notifications"]["weeklyDigest"] is False
    store.save_settings(u.id, {"theme": "light"})                # upsert replaces the stored blob
    assert store.get_settings(u.id) == {"theme": "light"}
    # settings live in their own table — saving them never creates report/onboarding state
    assert store.latest_report(u.id) is None and store.get_onboarding(u.id) is None


# --------------------------------------------------------------------------- #
# scored-JSON sanitisation — non-finite floats must never reach SQLite
#
# Regression: scored reads carry NaN sentinels by design (confidence/register default to NaN;
# an unknown outlet scores lean as NaN). json.dumps at its default allow_nan=True writes the
# bare tokens NaN/Infinity/-Infinity, which are invalid JSON — SQLite's json_extract index
# (on feed_articles.scored) then rejects the row with "malformed JSON" and ingestion of every
# feed fails. The fix routes every scored write through _dumps_scored -> _json_safe, which maps
# non-finite floats to null. These tests pin that invariant.
# --------------------------------------------------------------------------- #
def _no_json_constants(token):
    """parse_constant hook: json.loads accepts NaN/Infinity by default, so raise on them to
    assert the string is strictly RFC-8259-valid."""
    raise ValueError(f"non-RFC JSON constant present: {token!r}")


def test_json_safe_maps_non_finite_to_none():
    js = store_mod._json_safe
    assert js(float("nan")) is None
    assert js(float("inf")) is None
    assert js(float("-inf")) is None
    # finite floats, ints, strings, bools and None pass through byte-for-byte
    assert js(1.5) == 1.5 and js(-2.0) == -2.0 and js(0.0) == 0.0
    assert js(7) == 7 and js("x") == "x" and js(None) is None
    assert js(True) is True and js(False) is False        # bools are not coerced via the float branch


def test_json_safe_recurses_nested_dicts_and_lists():
    js = store_mod._json_safe
    payload = {
        "lean": float("nan"),
        "confidence": float("-inf"),
        "outlet": "NPR",
        "finite": 1.25,
        "emotion": {"fear": float("nan"), "anger": 0.5, "joy": float("inf")},   # nested dict
        "vec": [1.0, float("nan"), -3.0, float("-inf")],                          # nested list
        "nested": {"deep": [{"v": float("inf")}]},                               # dict->list->dict
    }
    out = js(payload)
    assert out["lean"] is None and out["confidence"] is None
    assert out["outlet"] == "NPR" and out["finite"] == 1.25          # non-float + finite untouched
    assert out["emotion"] == {"fear": None, "anger": 0.5, "joy": None}
    assert out["vec"] == [1.0, None, -3.0, None]
    assert out["nested"] == {"deep": [{"v": None}]}
    # the original object is not mutated
    assert math.isnan(payload["lean"]) and math.isnan(payload["emotion"]["fear"])


def test_dumps_scored_is_rfc_valid_json():
    payload = store_mod._dumps_scored(
        {"lean": float("nan"), "confidence": float("inf"), "emotion": {"fear": float("-inf")},
         "outlet": "BBC", "score": 0.7})
    assert "NaN" not in payload and "Infinity" not in payload      # no bare non-RFC tokens
    # strict parse: raises if any NaN/Infinity constant slipped through
    parsed = json.loads(payload, parse_constant=_no_json_constants)
    assert parsed == {"lean": None, "confidence": None, "emotion": {"fear": None},
                      "outlet": "BBC", "score": 0.7}


def _sqlite_json_valid(store, table, column="scored"):
    """Every stored `column` value, plus SQLite's own json_valid() verdict for each row."""
    with store.session() as s:
        rows = s.execute(store_mod.text(
            f"SELECT {column}, json_valid({column}) FROM {table}")).all()
    return rows


def test_feed_article_scored_is_json_valid_with_nan(store):
    """The exact failing path: a FeedArticle whose scored dict carries NaN (as every offline
    scoring does) must persist and read back as valid JSON — json_valid(scored) == 1."""
    scored = {"article_id": "https://npr.example/a", "outlet": "NPR", "lean": float("nan"),
              "confidence": float("nan"), "register": float("nan"), "category": "Politics",
              "emotion": {"fear": float("nan")}}
    created = store.upsert_feed_article(
        canonical_url="https://npr.example/a", url="https://npr.example/a", publisher="NPR",
        source_publisher="NPR", title="Headline", description="", body=None,
        published_at="2026-07-01T00:00:00Z", source_feed="f", scored=scored)
    assert created is True
    rows = _sqlite_json_valid(store, "feed_articles")
    assert len(rows) == 1
    stored_text, valid = rows[0]
    assert valid == 1                                              # SQLite accepts it as JSON
    assert "NaN" not in stored_text                                # the invalid token is gone
    # read-back: NaN sentinels surface as None; the outlet/category are intact
    back = store.get_feed_article("https://npr.example/a")["scored"]
    assert back["lean"] is None and back["confidence"] is None and back["emotion"]["fear"] is None
    assert back["outlet"] == "NPR" and back["category"] == "Politics"


def test_feed_article_finite_scored_unchanged(store):
    """A fully-finite scored dict is stored and read back with its values intact (the fix only
    touches non-finite floats)."""
    scored = {"article_id": "https://wsj.example/b", "outlet": "WSJ", "lean": 0.9,
              "confidence": 0.83, "category": "Business"}
    store.upsert_feed_article(
        canonical_url="https://wsj.example/b", url="https://wsj.example/b", publisher="WSJ",
        source_publisher="WSJ", title="Markets", description="", body=None,
        published_at="2026-07-01T00:00:00Z", source_feed="f", scored=scored)
    _, valid = _sqlite_json_valid(store, "feed_articles")[0]
    assert valid == 1
    back = store.get_feed_article("https://wsj.example/b")["scored"]
    assert back["lean"] == 0.9 and back["confidence"] == 0.83 and back["outlet"] == "WSJ"


def test_scored_cache_stores_valid_json(store):
    """save_scored_article (the scoring cache) also sanitises: valid JSON, NaN -> None on read."""
    store.save_scored_article("https://cnn.example/c",
                              {"outlet": "CNN", "lean": float("nan"), "confidence": 0.4})
    _, valid = _sqlite_json_valid(store, "scored_articles")[0]
    assert valid == 1
    got = store.get_scored_article("https://cnn.example/c")
    assert got["lean"] is None and got["confidence"] == 0.4 and got["outlet"] == "CNN"


def test_reads_store_valid_json(store):
    """add_read persists reader scored payloads as valid JSON too (single serialisation path)."""
    u = store.upsert_user_by_identity("google", "san-reads")
    store.add_read(u.id, "https://fox.example/d",
                   {"article_id": "https://fox.example/d", "outlet": "Fox News",
                    "lean": float("inf"), "confidence": float("nan")}, "2026-07-01T00:00:00Z")
    _, valid = _sqlite_json_valid(store, "reads")[0]
    assert valid == 1
    back = store.get_reads(u.id)[0]
    assert back["lean"] is None and back["confidence"] is None and back["outlet"] == "Fox News"
