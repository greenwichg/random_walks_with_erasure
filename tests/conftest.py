"""Suite-wide fixtures.

Story construction draws its clustering candidates from a **rolling time window**
(``story_service.scan_days``, default 6 days) rather than a fixed row count — the fix for story
yield tracking ingestion rate instead of the catalog.

Most fixtures across this suite build catalogs at fixed calendar dates, because what they assert is
clustering, coverage, filters or serialization — not retention. Under a real 6-day window those
corpora age out and the assertions collapse against an empty catalog, which tests nothing and breaks
a little more each day that passes.

So the window is opened wide for the suite by default. The window's OWN behaviour is covered
explicitly in ``test_story_service.py`` (which drops this override to exercise the real default),
and any test may override it locally with ``monkeypatch.setenv``.
"""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _wide_story_scan_window():
    """Keep fixed-date fixtures inside the story scan window for the whole session."""
    prior = os.environ.get("RWE_STORIES_SCAN_DAYS")
    os.environ["RWE_STORIES_SCAN_DAYS"] = "36500"     # ~100 years: date-pinned corpora stay in scope
    yield
    if prior is None:
        os.environ.pop("RWE_STORIES_SCAN_DAYS", None)
    else:
        os.environ["RWE_STORIES_SCAN_DAYS"] = prior


@pytest.fixture(autouse=True)
def _push_worker_is_running():
    """Start every test with a push worker that is not shutting down.

    ``push_delivery._stop`` is module state that outlives an application lifespan: ``shutdown()``
    sets it and ``startup()`` clears it, which is exactly right for a process that boots once. This
    suite is not that process — it runs many simulated lifespans in one interpreter, so a test client
    whose teardown calls ``shutdown()`` leaves every later test talking to a worker that has been
    told to stop, and a delivery test then fails with an empty run and no explanation.

    Cleared before AND after: before, so a test is unaffected by whatever ran previously; after, so a
    test that deliberately stops the worker cannot leak that decision into its neighbours.
    """
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "examples"))
    import push_delivery
    push_delivery._stop.clear()
    yield
    push_delivery._stop.clear()


@pytest.fixture(autouse=True)
def _fresh_story_cache():
    """Start every test with an empty story cache — the serve-stale analogue of the push fixture
    above, for the same reason: module state outliving what a single test believes it set up.

    Since serve-stale (P0-1), a reader who finds a stale entry is handed the PREVIOUS build while a
    real daemon thread rebuilds behind them. In production that is the fix; in a suite it is
    pollution — a store reused across tests carries the previous test's build, so an
    ingest-then-list test reads the old catalog and races a refresh thread it never started.
    Clearing turns every test's first read into a cold (fresh) build, which is the pre-P0-1
    behaviour every existing ingest-then-assert test was written against. Tests that exercise the
    stale path itself opt in by warming first (see test_story_service.py's serve-stale block).

    Before AND after, like its neighbour: before isolates from history, after keeps a test that
    left a pending refresh from leaking it forward."""
    import story_service
    story_service.clear_cache()
    yield
    story_service.clear_cache()
