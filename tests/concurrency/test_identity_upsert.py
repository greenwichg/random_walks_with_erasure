"""Invariant suite for `Store.upsert_user_by_identity`.

Every property here is one of the invariants in **docs/IDENTITY_UPSERT_CONCURRENCY.md §1**, asserted
against the shipped method. Until the transaction-retry algorithm landed this file also carried an
executable reference implementation of §4, and `test_I8_...` was an `xfail(strict=True)` on the shipped
method; both are gone now that the shipped method *is* §4 — that removal was the point of the tripwire.

Baseline: SQLAlchemy 2.0.51, SQLite 3.45.1, Python 3.11.15 / 3.12.3.

Why this suite is shaped this way, and what to do when it goes red: docs/CONCURRENCY_TESTING.md
"""

import pytest
from sqlalchemy.exc import IntegrityError

from harness import (ACCOUNT, PROVIDER, counts, find_identity, read_barrier, run_concurrently,
                     store_mod)

pytestmark = pytest.mark.concurrency


def upsert(store, provider=PROVIDER, account_id=ACCOUNT, email=None, display_name=None,
           refresh_profile=True):
    """The subject, with this suite's default identity pre-filled."""
    return store.upsert_user_by_identity(provider, account_id, email=email,
                                         display_name=display_name,
                                         refresh_profile=refresh_profile)


# --------------------------------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------------------------------
def test_I2_sequential_calls_are_idempotent(file_store):
    ids = {upsert(file_store, email=f"a{k}@x.io").id for k in range(5)}
    assert len(ids) == 1
    assert counts(file_store) == (1, 1)


def test_I5_same_email_under_two_providers_yields_two_users(file_store):
    """The anti-hijack invariant. Must fail loudly if the join key ever becomes email."""
    a = upsert(file_store, provider="google", account_id="g-1", email="same@x.io")
    b = upsert(file_store, provider="dev", account_id="d-1", email="same@x.io")
    assert a.id != b.id
    assert counts(file_store) == (2, 2)


def test_profile_columns_are_only_written_when_supplied(file_store):
    upsert(file_store, email="first@x.io", display_name="First")
    again = upsert(file_store, email=None, display_name=None)
    assert again.email == "first@x.io" and again.display_name == "First"


def test_Q2_the_returned_user_is_readable_after_the_session_closes(file_store):
    user = upsert(file_store, email="a@x.io", display_name="A")
    assert (user.id, user.email, user.display_name) == (user.id, "a@x.io", "A")


def test_Q5_a_failure_after_the_inserts_commits_nothing(file_store):
    """The test the withdrawn savepoint design would have failed (§9.7). Exercises the store's own
    session helper, which is what rolls back on any exception."""
    with pytest.raises(RuntimeError):
        with file_store.session() as s:
            user = store_mod.User(email="w@x.io")
            s.add(user)
            s.flush()
            s.add(store_mod.Identity(provider=PROVIDER, provider_account_id=ACCOUNT,
                                     user_id=user.id))
            s.flush()
            raise RuntimeError("fails before commit")
    assert counts(file_store) == (0, 0)


@pytest.mark.parametrize("n", [2, 8, 15])
def test_I1_I3_I4_concurrent_first_sighting_never_duplicates(file_store, n):
    """The core race. All N miss the read before any of them writes — the only interleaving that
    produces a first-sighting conflict (§9.5 cases 1–2)."""
    read_barrier([file_store.engine], n)
    ids, errors = run_concurrently(n, lambda: upsert(file_store, email="t@x.io").id)
    assert counts(file_store) == (1, 1), f"duplicates or an orphan: {counts(file_store)}"
    assert len(set(ids)) == 1, f"callers disagreed about the user id: {sorted(set(ids))}"
    assert errors == [], f"a caller failed rather than resolving: {sorted(set(errors))}"


@pytest.mark.parametrize("n", [2, 15])
def test_I8_every_concurrent_caller_resolves(file_store, n):
    """I8: losing the race produces a user, not an error.

    This was an `xfail(strict=True)` while the shipped method still raised on a lost race — 5 of 15
    callers took an `IntegrityError`. It is a plain passing test now, which is what "the tripwire fired
    and the work landed" looks like.
    """
    read_barrier([file_store.engine], n)
    ids, errors = run_concurrently(n, lambda: upsert(file_store, email="t@x.io").id)
    assert not errors, f"{len(errors)} of {n} callers failed: {sorted(set(errors))}"
    assert len(ids) == n and len(set(ids)) == 1


def test_I6_a_failure_no_winner_explains_is_re_raised(file_store, monkeypatch):
    """I6: given a first-attempt failure and no winner in the store, the ORIGINAL exception surfaces
    rather than being reported as success.

    The failure is injected at the private per-attempt method, because the only way to provoke a
    non-race `IntegrityError` through the public signature would be to corrupt the schema. Pairs with
    `test_SC8_foreign_key_violation_also_raises_integrity_error`, which establishes that a non-race
    failure really does arrive as `IntegrityError`: together they cover the rule and its trigger.
    """
    injected = IntegrityError("INSERT ...", {}, Exception("not a race — a different constraint"))
    attempts = []
    real = store_mod.Store._resolve_identity

    def fake(self, provider, account_id, email, display_name, *, create, refresh_profile=True):
        attempts.append(create)
        if create:
            raise injected
        return real(self, provider, account_id, email, display_name, create=False,
                    refresh_profile=refresh_profile)

    monkeypatch.setattr(store_mod.Store, "_resolve_identity", fake)

    with pytest.raises(IntegrityError) as excinfo:
        upsert(file_store, email="a@x.io")

    assert excinfo.value is injected, "a different exception surfaced — the original was swallowed"
    assert attempts == [True, False], "the second attempt must run before the re-raise"
    assert counts(file_store) == (0, 0)


def test_I6_a_failure_with_a_winner_resolves_instead_of_raising(file_store, monkeypatch):
    """The other side of I6: the same injected failure, but with a winner already present, resolves."""
    winner = upsert(file_store, email="w@x.io")

    injected = IntegrityError("INSERT ...", {}, Exception("looks like a race"))
    real = store_mod.Store._resolve_identity

    def fake(self, provider, account_id, email, display_name, *, create, refresh_profile=True):
        if create:
            raise injected
        return real(self, provider, account_id, email, display_name, create=False,
                    refresh_profile=refresh_profile)

    monkeypatch.setattr(store_mod.Store, "_resolve_identity", fake)

    resolved = upsert(file_store, email="l@x.io")
    assert resolved.id == winner.id
    assert counts(file_store) == (1, 1)


def test_a_lost_race_leaves_no_gap_in_the_profile_columns(file_store):
    """The loser's second attempt still applies its own email/display name — profile context is
    last-write-wins (I5), and losing the race must not mean losing the write."""
    read_barrier([file_store.engine], 2)
    ids, errors = run_concurrently(2, lambda: upsert(file_store, email="racer@x.io",
                                                     display_name="Racer").id)
    assert not errors and len(set(ids)) == 1
    user = file_store.get_user(ids[0])
    assert (user.email, user.display_name) == ("racer@x.io", "Racer")


def test_OB4_exactly_one_caller_wins_and_the_rest_take_the_loser_path(file_store, monkeypatch):
    """Proof that the race tests are exercising the race at all.

    Agreeing ids are not enough on their own: they would agree just as well if the callers had
    serialised and every one of them had taken the hit-the-read path. So this counts second attempts
    directly — exactly N-1 of them, one per loser. If this ever reports 0, every other test in this
    file is passing without a conflict having occurred, which is the failure mode
    docs/CONCURRENCY_TESTING.md §4 is about.
    """
    n = 8
    real = store_mod.Store._resolve_identity
    second_attempts = []

    def counting(self, provider, account_id, email, display_name, *, create,
                 refresh_profile=True):
        if not create:
            second_attempts.append(provider)
        return real(self, provider, account_id, email, display_name, create=create,
                    refresh_profile=refresh_profile)

    monkeypatch.setattr(store_mod.Store, "_resolve_identity", counting)

    read_barrier([file_store.engine], n)
    ids, errors = run_concurrently(n, lambda: upsert(file_store, email="t@x.io").id)

    assert not errors and len(ids) == n
    assert len(set(ids)) == 1, "every caller must hold the same id — one winner, N-1 resolvers"
    assert len(second_attempts) == n - 1, (
        f"expected exactly {n - 1} losers to take the retry path, saw {len(second_attempts)} — "
        "if 0, the barrier is not producing a conflict and these tests prove nothing"
    )
    assert counts(file_store) == (1, 1)


def test_R4_two_engines_on_one_file_race_safely(file_store, second_store):
    """The multi-process model (§9.8 R4): independent engines and pools over the same file, racing.

    One thread per Store, so the conflict crosses pools rather than being serialised inside one.
    """
    import threading

    stores = [file_store, second_store()]
    read_barrier([s.engine for s in stores], len(stores))   # one barrier spanning both pools
    resolved, errors = [], []

    def target(store):
        try:
            resolved.append(upsert(store, email="t@x.io").id)
        except BaseException as exc:                     # noqa: BLE001
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=target, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert not errors, errors
    assert len(set(resolved)) == 1, f"the two engines disagreed: {resolved}"
    assert counts(file_store) == (1, 1)


# --------------------------------------------------------------------------------------------------
# refresh_profile (S2) — the flag must not weaken any invariant above.
# --------------------------------------------------------------------------------------------------
def test_I1_I2_concurrent_first_sighting_with_refresh_profile_false(file_store):
    """The identity invariants hold identically with the refresh suppressed.

    Recovery is the caller that passes `refresh_profile=False`, and recovery is also what makes
    concurrent first-sightings of one identity likely (§9c of the recovery design). So the two have
    to be exercised together: N callers, no profile writes on the losers' retry, still one user."""
    n = 8
    read_barrier([file_store.engine], n)
    ids, errors = run_concurrently(
        n, lambda: upsert(file_store, email="r@x.io", display_name="R",
                          refresh_profile=False).id)
    assert errors == [], f"a caller failed rather than resolving: {sorted(set(errors))}"
    assert len(set(ids)) == 1, f"{n} concurrent first-sightings produced {len(set(ids))} users"
    assert counts(file_store) == (1, 1), f"duplicates or an orphan: {counts(file_store)}"


def test_refresh_profile_false_leaves_the_losers_retry_read_only(file_store):
    """The retry stops writing on this path.

    `_resolve_identity(create=False)` applies the profile refresh too, so with the sign-in default a
    loser can emit an UPDATE. With the flag off it cannot — the whole second transaction is a SELECT.
    That narrows the open item in docs/IDENTITY_UPSERT_CONCURRENCY.md rather than closing it: the
    default path is unchanged and can still write."""
    from sqlalchemy import event

    upsert(file_store, email="first@x.io", display_name="First")

    seen = []
    event.listen(file_store.engine, "after_cursor_execute",
                 lambda conn, cur, stmt, params, ctx, many: seen.append(stmt.split()[0].upper()))

    ids, errors = run_concurrently(
        8, lambda: upsert(file_store, email="late@x.io", display_name="Late",
                          refresh_profile=False).id)
    assert errors == []
    assert len(set(ids)) == 1
    assert seen.count("UPDATE") == 0, f"expected no writes, saw {seen.count('UPDATE')}"
    with file_store.session() as s:
        assert find_identity(s) is not None
    assert file_store.get_user(ids[0]).display_name == "First", "the stored profile survived"
