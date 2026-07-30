"""Version-drift detectors for the storage premises the identity upsert rests on.

Each test is named for the assumption it pins in
**docs/IDENTITY_UPSERT_CONCURRENCY.md §10** — `SC*` Stable Contract, `OB*` Observed Behavior,
`ID*` Implementation Detail. A failure here is not necessarily a bug in this repository: it may mean
SQLite, Python, SQLAlchemy or the sqlite3 driver changed under us. **The test's own message says which
section to re-read.** That is the point of keeping these.

Measured baseline when written: SQLAlchemy 2.0.51, SQLite 3.45.1, Python 3.11.15 / 3.12.3 (both CI
targets), sqlite3 in legacy transaction control.

Run the fast ones with the normal suite; the two wall-clock ones are marked `slow` and excluded by
default (`pytest -m slow tests/concurrency` to include them).

Why this suite is shaped this way, and what to do when it goes red: docs/CONCURRENCY_TESTING.md
"""

import time

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from harness import ACCOUNT, PROVIDER, counts, find_identity, store_mod

pytestmark = pytest.mark.concurrency


# --------------------------------------------------------------------------------------------------
# The configuration the whole contract is stated against (§5). If this drifts, every number in the
# document is describing a machine that no longer exists.
# --------------------------------------------------------------------------------------------------
def test_engine_configuration_matches_the_contract(file_store, tmp_path):
    with file_store.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert c.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    assert type(file_store.engine.pool).__name__ == "QueuePool", "file-backed engines pool connections"
    assert file_store._Session.kw["expire_on_commit"] is False, "Q2: returned instances stay readable"

    in_memory = store_mod.Store("sqlite:///:memory:")
    assert type(in_memory.engine.pool).__name__ == "StaticPool", (
        "the in-memory fixture shares ONE connection, which is why race tests must be file-backed "
        "(docs/IDENTITY_UPSERT_CONCURRENCY.md §7)"
    )


def test_unique_constraint_exists_on_the_identity_pair(file_store):
    """SC1 — the arbiter of I1/I3. `create_all` never adds a constraint to an existing table, so this
    also documents what the ops check on the live database is looking for (§7)."""
    with file_store.engine.connect() as c:
        indexes = c.exec_driver_sql("PRAGMA index_list('identities')").fetchall()
        unique = [row for row in indexes if row[2]]                      # row[2] = "unique" flag
        assert unique, f"no unique index on identities: {indexes}"
        columns = set()
        for row in unique:
            info = c.exec_driver_sql(f"PRAGMA index_info('{row[1]}')").fetchall()
            columns |= {col[2] for col in info}
        assert {"provider", "provider_account_id"} <= columns


# --------------------------------------------------------------------------------------------------
# Stable Contract
# --------------------------------------------------------------------------------------------------
def test_SC4_transaction_rollback_discards_every_statement(file_store):
    """I4 and Q5 rest on this and nothing else. The withdrawn savepoint design rested on something
    weaker, which is why it broke."""
    s = file_store._Session()
    user = store_mod.User(email="w@x.io")
    s.add(user)
    s.flush()
    s.add(store_mod.Identity(provider=PROVIDER, provider_account_id=ACCOUNT, user_id=user.id))
    s.flush()
    assert counts(file_store) == (0, 0), "SC5: uncommitted work is invisible to other connections"
    s.rollback()
    assert counts(file_store) == (0, 0), "SC4: rollback discarded both inserts"
    s.close()


def test_SC5_committed_rows_are_visible_to_later_transactions(file_store):
    """Why the loser's SECOND attempt is guaranteed to find the winner (§3.3). Ordinary visibility —
    which is precisely why the redesign does not need an isolation-level caveat on PostgreSQL."""
    writer = file_store._Session()
    user = store_mod.User(email="w@x.io")
    writer.add(user)
    writer.flush()
    writer.add(store_mod.Identity(provider=PROVIDER, provider_account_id=ACCOUNT, user_id=user.id))
    writer.commit()
    writer.close()

    reader = file_store._Session()                       # a transaction that starts afterwards
    assert find_identity(reader) is not None
    reader.close()


def test_SC8_foreign_key_violation_also_raises_integrity_error(file_store):
    """Why I6 must discriminate: `IntegrityError` alone does not mean "lost the race"."""
    s = file_store._Session()
    s.add(store_mod.Identity(provider=PROVIDER, provider_account_id="fk-probe", user_id=999_999))
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()
    s.close()
    assert counts(file_store) == (0, 0)


# --------------------------------------------------------------------------------------------------
# Implementation Detail — the tripwires
# --------------------------------------------------------------------------------------------------
def test_ID1_sqlite3_is_still_in_legacy_transaction_control(file_store):
    """A SELECT must open no transaction; the first DML must open one.

    SQLAlchemy's SQLite dialect documentation: the sqlite3 driver "defaults (which will continue
    through Python 3.15 before being removed in Python 3.16) to legacy transactional behavior".

    IF THIS FAILS: the driver's transaction mode changed (Python >= 3.16, or someone set
    `isolation_level` / `connect_args={"autocommit": ...}`). Re-read
    docs/IDENTITY_UPSERT_CONCURRENCY.md §10 ID1 and re-validate OB1 — a lost race may now surface as
    `OperationalError` (a snapshot conflict) rather than `IntegrityError`. The specified algorithm
    catches both, so this is a prompt to re-verify, not necessarily a defect.
    """
    s = file_store._Session()
    find_identity(s)                                     # a bare SELECT
    dbapi = s.connection().connection
    assert dbapi.in_transaction is False, "a read opened a transaction — the driver mode changed"
    s.add(store_mod.User(email="x@x.io"))
    s.flush()                                            # first DML
    assert dbapi.in_transaction is True, "DML did not open a transaction"
    s.rollback()
    s.close()


def test_ID2_a_released_savepoint_escapes_the_enclosing_rollback(file_store):
    """The documented misbehaviour that disqualified the savepoint design.

    SQLAlchemy's SQLite dialect documentation, legacy transaction mode: "Incorrect behavior for
    SAVEPOINT - as the SAVEPOINT statement does not imply a BEGIN, a new SAVEPOINT emitted before a
    BEGIN will function on its own but fails to participate in the enclosing transaction, meaning a
    ROLLBACK of the transaction will not rollback elements that were part of a released savepoint."

    So the rows are committed at RELEASE, before `COMMIT` is ever reached, and the rollback that
    `Store.session()` performs on any exception cannot undo them. That violates Q5.

    IF THIS FAILS: savepoints now participate properly (a driver-mode change, or a SQLAlchemy fix).
    The specified algorithm does not use savepoints and is unaffected — but §10 ID2 can then be
    reclassified, and a savepoint-scoped design becomes available again if there is ever a reason to
    want one.
    """
    s = file_store._Session()
    find_identity(s)                                     # the algorithm's initial read: no BEGIN
    with s.begin_nested():                               # SAVEPOINT with no transaction in effect
        user = store_mod.User(email="w@x.io")
        s.add(user)
        s.flush()
        s.add(store_mod.Identity(provider=PROVIDER, provider_account_id=ACCOUNT, user_id=user.id))
        s.flush()

    escaped = counts(file_store)
    s.rollback()                                         # exactly what Store.session() does on error
    after = counts(file_store)
    s.close()

    assert escaped == (1, 1), (
        "the released savepoint's rows were NOT visible to another connection before COMMIT — "
        "savepoint semantics changed; re-read §10 ID2"
    )
    assert after == (1, 1), (
        "the outer rollback DID undo the released savepoint — savepoint semantics changed; "
        "re-read §10 ID2 and §9.7"
    )


# --------------------------------------------------------------------------------------------------
# Observed Behavior
# --------------------------------------------------------------------------------------------------
@pytest.mark.slow
def test_OB2_busy_timeout_bounds_a_blocked_writer(file_store):
    """A writer blocked by a holder waits ~busy_timeout, then raises. Case 5 of §9.5 — and the reason
    callers must not hold a write transaction across slow work (R6). ~5 s of wall clock."""
    holder = file_store._Session()
    holder.add(store_mod.User(email="holder@x.io"))
    holder.flush()                                       # takes the single write lock, keeps it

    waiter = file_store._Session()
    started = time.monotonic()
    with pytest.raises(OperationalError) as excinfo:
        waiter.add(store_mod.User(email="waiter@x.io"))
        waiter.flush()
    waited = time.monotonic() - started

    assert "locked" in str(excinfo.value.orig).lower()
    # Only the LOWER bound is asserted: it must have waited for the lock rather than failing fast.
    # No upper bound, so a loaded machine cannot make this flaky.
    assert waited >= 4.0, f"busy_timeout is 5000 ms but it gave up after {waited:.2f}s"
    holder.rollback(); waiter.rollback(); holder.close(); waiter.close()


def test_OB2b_a_blocked_writer_proceeds_once_the_holder_commits(file_store):
    """The other half of OB2: contention is latency, not error, when the holder is quick. ~1 s."""
    import threading

    holder = file_store._Session()
    holder.add(store_mod.User(email="holder@x.io"))
    holder.flush()
    threading.Timer(0.8, holder.commit).start()

    waiter = file_store._Session()
    started = time.monotonic()
    waiter.add(store_mod.User(email="waiter@x.io"))
    waiter.flush()
    waiter.commit()
    waited = time.monotonic() - started

    # It blocked (>= the holder's hold time) and it acquired — not raising is itself proof that it
    # acquired inside busy_timeout, so no upper bound is needed and this cannot flake under load.
    assert waited >= 0.5, f"did not actually block on the holder; waited {waited:.2f}s"
    assert counts(file_store)[0] == 2
    holder.close(); waiter.close()


@pytest.mark.slow
def test_OB3_pool_ceiling_is_pool_size_plus_max_overflow(file_store):
    """15 connections (5 + 10). Nothing in the algorithm depends on this, but every barrier-style test
    in this directory does: at 16 holders the checkout blocks and the barrier never releases.

    IF THIS FAILS: the pool defaults changed. Update the `n <= 15` guard in conftest.run_concurrently
    and the note in §7. ~4 s of wall clock (a shortened checkout timeout).
    """
    import threading

    pool = file_store.engine.pool
    ceiling = pool.size() + pool._max_overflow
    assert ceiling == 15, f"pool ceiling is now {ceiling}; §7's 'keep N <= 15' needs updating"

    pool._timeout = 2                                    # fail fast instead of the 30 s default
    barrier = threading.Barrier(ceiling + 1, timeout=6)
    failures = []

    def hold():
        try:
            s = file_store._Session()
            find_identity(s)                             # checks out a connection and keeps it
            barrier.wait()
            s.close()
        except BaseException as exc:                     # noqa: BLE001
            failures.append(type(exc).__name__)

    threads = [threading.Thread(target=hold) for _ in range(ceiling + 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert failures, "ceiling+1 concurrent holders should not all succeed"


def test_OB5_sqlite_reuses_a_rowid_after_a_rolled_back_insert(file_store):
    """Recorded so nobody is surprised, and so §2's "never infer anything from an id" stays honest:
    SQLite reuses the id, PostgreSQL's sequences would not. Nothing depends on either."""
    s = file_store._Session()
    doomed = store_mod.User(email="doomed@x.io")
    s.add(doomed)
    s.flush()
    assert doomed.id == 1
    s.rollback()
    s.close()

    s2 = file_store._Session()
    kept = store_mod.User(email="kept@x.io")
    s2.add(kept)
    s2.flush()
    s2.commit()
    assert kept.id == 1, "SQLite reused the rowid; on PostgreSQL this would be 2"
    s2.close()


def test_two_stores_on_one_file_share_the_constraint(file_store, second_store, tmp_path):
    """R4's model of a second process: an independent engine and pool over the same file. The unique
    index is enforced by the database, so it is not per-connection or per-pool."""
    other = second_store()
    a = file_store.upsert_user_by_identity(PROVIDER, ACCOUNT, email="a@x.io")
    b = other.upsert_user_by_identity(PROVIDER, ACCOUNT, email="b@x.io")
    assert a.id == b.id
    assert counts(file_store) == (1, 1)
