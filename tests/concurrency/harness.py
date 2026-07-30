"""Shared helpers for the storage-level concurrency harness.

The one rule that makes this directory different from `tests/test_store.py`: **every store here is
file-backed**. The in-memory URL builds a `StaticPool`, which shares a single connection across
threads, so concurrent sessions serialise and a write conflict cannot occur at all. A race test on
that fixture passes for the wrong reason — see docs/IDENTITY_UPSERT_CONCURRENCY.md §7.
"""

import importlib.util
import pathlib
import sys
import threading

from sqlalchemy import event, func, select

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _load_store():
    """Load examples/store.py the same way tests/test_store.py does (it is not an installed module)."""
    spec = importlib.util.spec_from_file_location("store", ROOT / "examples" / "store.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["store"] = mod
    spec.loader.exec_module(mod)
    return mod


store_mod = _load_store()

PROVIDER = "google"
ACCOUNT = "108461123456789012345"


def counts(store):
    """(users, identities) as COMMITTED — read on a separate connection, so uncommitted work is
    invisible. Reading through the store's own session would see its pending writes."""
    with store.engine.connect() as c:
        return (
            c.execute(select(func.count()).select_from(store_mod.User.__table__)).scalar(),
            c.execute(select(func.count()).select_from(store_mod.Identity.__table__)).scalar(),
        )


def find_identity(session, provider=PROVIDER, account=ACCOUNT):
    return session.scalar(
        select(store_mod.Identity).where(
            store_mod.Identity.provider == provider,
            store_mod.Identity.provider_account_id == account,
        )
    )


def read_barrier(engines, n, timeout=20):
    """Make N callers deterministically miss the same identity read before any of them writes.

    Synchronising in the test body does not work: the method under test issues its OWN `SELECT` after
    the barrier, so with a small N one caller can complete its whole upsert before another even reads —
    the read hits, no conflict occurs, and the "race" test silently tests nothing. (Observed as a
    flaky XPASS at N=2 while writing this suite.)

    So the barrier is installed *inside* the engine, on the statement that matters: every caller is held
    immediately after its identities SELECT until all N have read. Each thread waits at most once, so a
    second attempt's re-read (the reference algorithm's recovery path) passes straight through.
    """
    barrier = threading.Barrier(n, timeout=timeout)
    seen = threading.local()
    remaining = {"n": n}
    gate = threading.Lock()

    def _sync(conn, cursor, statement, parameters, context, executemany):
        # DISARM after n waiters. A threading.Barrier *resets* once its parties pass, so leaving this
        # armed would trap the next caller of this SELECT — including the test's own `counts()` helper,
        # which reads `identities` on the same engine. (Observed as BrokenBarrierError while writing it.)
        if not remaining["n"] or getattr(seen, "waited", False):
            return
        if not (statement.lstrip().upper().startswith("SELECT") and "identities" in statement.lower()):
            return
        with gate:
            if not remaining["n"]:
                return
            remaining["n"] -= 1
        seen.waited = True
        barrier.wait()

    for engine in engines:
        event.listen(engine, "after_cursor_execute", _sync)
    return barrier


def run_concurrently(n, work, timeout=30):
    """Run `work()` on n threads. Returns (results, exception_names).

    Pair with `read_barrier` when the point is a first-sighting race — that is what makes all N callers
    miss the read before any of them writes.

    n must stay <= the pool ceiling (15: pool_size 5 + max_overflow 10). At 16, threads holding a
    connection while waiting deadlock on checkout — a property of the harness, not the algorithm.
    """
    assert n <= 15, "n > pool ceiling would deadlock on connection checkout, not test the algorithm"
    results, errors = [], []
    lock = threading.Lock()

    def target():
        try:
            value = work()
            with lock:
                results.append(value)
        except BaseException as exc:                     # noqa: BLE001 — the type IS the observation
            with lock:
                errors.append(type(exc).__name__)

    threads = [threading.Thread(target=target) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 10)
    return results, errors
