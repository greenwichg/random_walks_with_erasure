"""Unit tests for examples/store.py — the beta persistence layer (SQLite / SQLAlchemy).

Uses an in-memory database (a shared ``StaticPool`` connection) so nothing touches disk;
verifies the identity upsert is idempotent and that users are retrievable by their engine id.
This module is not imported by the JSON API yet, so no other suite is affected.
"""

import importlib.util
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
