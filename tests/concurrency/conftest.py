"""Fixtures for the storage-level concurrency harness.

The one rule that makes this directory different from `tests/test_store.py`: **every store here is
file-backed**. The in-memory URL builds a `StaticPool`, which shares a single connection across
threads, so concurrent sessions serialise and a write conflict cannot occur at all. A race test on
that fixture passes for the wrong reason — see docs/IDENTITY_UPSERT_CONCURRENCY.md §7.
"""

import pytest

from harness import store_mod


@pytest.fixture
def file_store(tmp_path):
    """A fresh FILE-BACKED store per test — the only kind that can produce a write conflict."""
    return store_mod.Store(f"sqlite:///{tmp_path / 'probe.db'}")


@pytest.fixture
def second_store(tmp_path):
    """Opens a second Store over the SAME file, with its own engine and pool.

    Models a second OS process (the `ingest` container, a CLI, another web instance) as closely as one
    process can — see the R4 caveat in docs/IDENTITY_UPSERT_CONCURRENCY.md §9.8.
    """
    def _open():
        return store_mod.Store(f"sqlite:///{tmp_path / 'probe.db'}")
    return _open
