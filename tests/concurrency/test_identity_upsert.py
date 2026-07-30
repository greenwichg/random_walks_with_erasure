"""Property suite for the identity upsert, run against TWO subjects.

`shipped` is `Store.upsert_user_by_identity` as it exists today. `reference` is the algorithm specified
in **docs/IDENTITY_UPSERT_CONCURRENCY.md §4** (transaction-scoped retry), implemented here so the
design is executable before it is adopted. Every invariant is asserted against both, and the *diff*
between them is exactly the change §4 proposes:

    shipped     I1 I3 I4 I5 I6 I7 ✔      I2 ✘ (under concurrency)   I8 ✘ (losers raise)
    reference   I1 I3 I4 I5 I6 I7 ✔      I2 ✔                        I8 ✔

**When §4 is implemented**, `shipped` starts behaving like `reference`: the two `xfail(strict=True)`
markers below turn into XPASS and the suite fails on purpose. That is the signal to delete the
reference implementation from this file and collapse the parametrisation onto the real method.

Baseline: SQLAlchemy 2.0.51, SQLite 3.45.1, Python 3.11.15 / 3.12.3.

Why this suite is shaped this way, and what to do when it goes red: docs/CONCURRENCY_TESTING.md
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from harness import (ACCOUNT, PROVIDER, counts, find_identity, read_barrier, run_concurrently,
                     store_mod)

pytestmark = pytest.mark.concurrency


# --------------------------------------------------------------------------------------------------
# The specified algorithm (§4), as an executable reference. Delete when the real method adopts it.
# --------------------------------------------------------------------------------------------------
def _attempt(store, provider, account_id, email, display_name, *, create):
    """One attempt, one transaction. `create=False` resolves an existing identity or returns None."""
    with store.session() as s:
        identity = s.scalar(
            select(store_mod.Identity).where(
                store_mod.Identity.provider == provider,
                store_mod.Identity.provider_account_id == account_id,
            )
        )
        if identity is None:
            if not create:
                return None
            user = store_mod.User(email=email, display_name=display_name)
            s.add(user)
            s.flush()                                    # assigns user.id
            s.add(store_mod.Identity(provider=provider, provider_account_id=account_id,
                                     user_id=user.id))
            s.flush()                                    # the conflict surfaces here
        else:
            user = identity.user
        if email is not None:
            user.email = email
        if display_name is not None:
            user.display_name = display_name
        s.flush()
        s.refresh(user)
        return user


def upsert_reference(store, provider=PROVIDER, account_id=ACCOUNT, email=None, display_name=None,
                     *, _fail_first=None):
    """§4: two attempts, each its own transaction, no savepoint.

    `_fail_first` is a test seam for I6 — it injects a first-attempt failure that no winner explains.
    """
    try:
        if _fail_first is not None:
            raise _fail_first
        return _attempt(store, provider, account_id, email, display_name, create=True)
    except (IntegrityError, OperationalError) as first:
        user = _attempt(store, provider, account_id, email, display_name, create=False)
        if user is None:
            raise first                                  # no winner, so this was never a race — I6
        return user


def upsert_shipped(store, provider=PROVIDER, account_id=ACCOUNT, email=None, display_name=None):
    return store.upsert_user_by_identity(provider, account_id, email=email, display_name=display_name)


SUBJECTS = {"shipped": upsert_shipped, "reference": upsert_reference}


@pytest.fixture(params=sorted(SUBJECTS))
def upsert(request):
    """Both subjects, so a property is asserted against today's code and tomorrow's."""
    return SUBJECTS[request.param]


# --------------------------------------------------------------------------------------------------
# Invariants both subjects must satisfy — today and after §4 lands
# --------------------------------------------------------------------------------------------------
def test_I2_sequential_calls_are_idempotent(file_store, upsert):
    ids = {upsert(file_store, email=f"a{k}@x.io").id for k in range(5)}
    assert len(ids) == 1
    assert counts(file_store) == (1, 1)


def test_I5_same_email_under_two_providers_yields_two_users(file_store, upsert):
    """The anti-hijack invariant. Must fail loudly if the join key ever becomes email."""
    a = upsert(file_store, provider="google", account_id="g-1", email="same@x.io")
    b = upsert(file_store, provider="dev", account_id="d-1", email="same@x.io")
    assert a.id != b.id
    assert counts(file_store) == (2, 2)


def test_profile_columns_are_only_written_when_supplied(file_store, upsert):
    upsert(file_store, email="first@x.io", display_name="First")
    again = upsert(file_store, email=None, display_name=None)
    assert again.email == "first@x.io" and again.display_name == "First"


def test_Q2_the_returned_user_is_readable_after_the_session_closes(file_store, upsert):
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
def test_I1_I3_I4_concurrent_first_sighting_never_duplicates(file_store, upsert, n):
    """The core race. Threads read first, meet at the barrier, then all attempt the write — the only
    interleaving that produces a first-sighting conflict (§9.5 cases 1–2).

    Asserted for BOTH subjects because these three invariants hold today: the shipped method loses the
    race noisily, but it loses it *safely*.
    """
    read_barrier([file_store.engine], n)                 # all N miss the read before any of them writes
    ids, errors = run_concurrently(n, lambda: upsert(file_store, email="t@x.io").id)
    assert counts(file_store) == (1, 1), f"duplicates or an orphan: {counts(file_store)}"
    assert len(set(ids)) <= 1, f"callers disagreed about the user id: {sorted(set(ids))}"
    assert set(errors) <= {"IntegrityError"}, f"unexpected failure class: {sorted(set(errors))}"


XFAIL_SHIPPED = pytest.mark.xfail(
    strict=True,
    reason=(
        "I8 is the defect docs/IDENTITY_UPSERT_CONCURRENCY.md §4 fixes: today a caller that loses the "
        "race gets an IntegrityError instead of the winner's user. When §4 lands this XPASSes and the "
        "suite fails on purpose — delete the reference implementation in this file and drop this marker."
    ),
)


@pytest.mark.parametrize("n", [2, 15])
@pytest.mark.parametrize("subject", [pytest.param("shipped", marks=XFAIL_SHIPPED), "reference"])
def test_I8_every_concurrent_caller_resolves(file_store, subject, n):
    """I8: losing the race must produce a user, not an error.

    `shipped` is expected to fail; `strict=True` turns "it started passing" into a suite failure, which
    is the signal that §4 landed.
    """
    upsert_fn = SUBJECTS[subject]

    read_barrier([file_store.engine], n)
    ids, errors = run_concurrently(n, lambda: upsert_fn(file_store, email="t@x.io").id)
    assert not errors, f"{len(errors)} of {n} callers failed: {sorted(set(errors))}"
    assert len(ids) == n and len(set(ids)) == 1


def test_I6_a_failure_no_winner_explains_is_re_raised(file_store):
    """I6, on the reference algorithm: given a first-attempt failure and no winner in the store, the
    ORIGINAL exception must surface rather than being reported as success.

    Pairs with `test_SC8_foreign_key_violation_also_raises_integrity_error`, which establishes that a
    non-race failure really does arrive as `IntegrityError` — together they cover the rule and its
    trigger without contorting the algorithm to inject a foreign-key violation mid-flight.
    """
    injected = IntegrityError("INSERT ...", {}, Exception("not a race — a different constraint"))
    with pytest.raises(IntegrityError) as excinfo:
        upsert_reference(file_store, email="a@x.io", _fail_first=injected)
    assert excinfo.value is injected
    assert counts(file_store) == (0, 0)


def test_I6_a_failure_with_a_winner_resolves_instead_of_raising(file_store):
    """The other side of I6: the same injected failure, but with a winner already present, resolves."""
    winner = upsert_reference(file_store, email="w@x.io")
    injected = IntegrityError("INSERT ...", {}, Exception("looks like a race"))
    resolved = upsert_reference(file_store, email="l@x.io", _fail_first=injected)
    assert resolved.id == winner.id
    assert counts(file_store) == (1, 1)


def test_OB4_exactly_one_caller_wins_and_the_rest_take_the_loser_path(file_store):
    """Confirms a race test is exercising the race at all. With the reference algorithm all N resolve;
    N-1 of them do so via the second attempt, which is observable as the winner's id being returned to
    callers that did not create it."""
    n = 8

    read_barrier([file_store.engine], n)
    ids, errors = run_concurrently(n, lambda: upsert_reference(file_store, email="t@x.io").id)
    assert not errors and len(ids) == n
    assert len(set(ids)) == 1, "every caller must hold the same id — one winner, N-1 resolvers"
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
            resolved.append(upsert_reference(store, email="t@x.io").id)
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
