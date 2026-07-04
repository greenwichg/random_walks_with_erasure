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
