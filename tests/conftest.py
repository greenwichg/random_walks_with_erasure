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

The other suite-wide rule here is about that same environment: **a module configures the engine for
itself and for nobody else.** The engine reads ``RWE_*`` at import and build time, so a variable one
module leaves behind silently reconfigures every module that runs after it — and the symptom is a
test that passes alone and fails in the suite, or vice versa, which is the most expensive kind of
failure to read. Function-scoped tests use ``monkeypatch``; module-scoped fixtures use ``module_env``
below; ``_no_env_leaks_between_modules`` makes a breach fail loudly instead of drifting downstream.
"""

import os

import pytest


def _engine_config():
    """The engine's configuration as it currently stands. ``RWE_*`` is the whole surface."""
    return {k: v for k, v in os.environ.items() if k.startswith("RWE_")}


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


@pytest.fixture(scope="module")
def module_env():
    """A monkeypatch that lives as long as a module-scoped fixture does.

    The built-in ``monkeypatch`` is function-scoped, so a module-scoped fixture that needs the engine
    configured a particular way cannot use it and has historically reached for a bare
    ``os.environ[...] = ...`` instead — which nothing ever undoes. That is not a tidiness point: the
    engine reads configuration from the environment at import and build time, so a leaked variable
    silently reconfigures every module that runs afterwards. The documented instance is in
    ``test_retention_narrow_projection.py`` — three coach fixtures leaked ``RWE_FEED_MIN_ARTICLES=5``
    and a deletion-policy test then measured ``minArticles`` as 5 instead of 50, passing alone and
    failing in the full suite.

    Use it exactly like ``monkeypatch`` (``module_env.setenv`` / ``.delenv``); everything is restored
    when the module's fixtures tear down. ``_no_env_leaks_between_modules`` below enforces the rule.

    It restores the whole ``RWE_*`` space rather than only the names it was told about, because the
    engine writes some of its own: ``api_fastapi._configure_recs_source`` sets ``RWE_QBIAS`` and
    ``RWE_PROFILE`` deliberately, so that the feed catalog becomes the authoritative corpus. A fixture
    cannot be expected to predict that, and monkeypatch alone would not undo it — ``delenv`` on a name
    that is absent records nothing, so a value the app creates afterwards has no recorded original to
    be rolled back to. Snapshotting the namespace needs no such prediction.
    """
    before = _engine_config()
    with pytest.MonkeyPatch.context() as mp:
        yield mp
    for k in set(_engine_config()) - set(before):
        del os.environ[k]
    os.environ.update(before)


@pytest.fixture(autouse=True)
def _engine_config_is_test_local():
    """No test may change the engine's configuration for the tests after it.

    ``monkeypatch`` already covers everything a test sets deliberately. This covers what the ENGINE
    sets on its way past: booting the app with the feed source on runs
    ``api_fastapi._configure_recs_source``, which writes ``RWE_QBIAS`` and ``RWE_PROFILE`` into the
    process environment on purpose — that is how the feed catalog becomes the authoritative corpus.
    Nothing recorded those, so nothing put them back, and every test after the first app boot ran
    against a corpus it had not chosen.

    Restoring to the state at test start rather than to some pristine baseline is what makes this
    safe next to module-scoped fixtures: pytest builds broader scopes first, so a module fixture's
    configuration is already in place when this snapshot is taken and survives untouched. Only what
    the test itself changed is rolled back.
    """
    before = _engine_config()
    yield
    for k in set(_engine_config()) - set(before):
        del os.environ[k]
    os.environ.update(before)


@pytest.fixture(autouse=True, scope="module")
def _no_env_leaks_between_modules(request):
    """No test module may change the engine's configuration for the modules after it.

    Autouse and module-scoped, so it is set up before that module's own fixtures and torn down after
    them: by the time this checks, every fixture the module used has finished cleaning up. A module
    that still shows a difference has leaked one, and the message names the variables.

    Scoped to ``RWE_*`` because that is the engine's whole configuration surface, and checked by
    value as well as by key — a module that overwrites a variable someone else set is the same bug as
    one that invents a new variable, and it reads identically downstream.
    """
    before = _engine_config()
    yield
    after = _engine_config()
    if before != after:
        changed = sorted(set(before) | set(after))
        diff = [f"{k}: {before.get(k)!r} -> {after.get(k)!r}"
                for k in changed if before.get(k) != after.get(k)]
        raise AssertionError(
            f"{request.node.name} leaked engine configuration into the rest of the suite:\n  "
            + "\n  ".join(diff)
            + "\n\nA module-scoped fixture must take the `module_env` fixture and use "
              "`module_env.setenv` / `.delenv`; a test or function-scoped fixture must use "
              "`monkeypatch`. A bare `os.environ[...] = ...` is never restored.")


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
    import sys
    # Self-sufficient on purpose: relying on the neighbour fixture's insert made this the first
    # fixture to fail whenever a test file (e.g. test_manage_users.py) ran standalone without any
    # module-level engine import — masked in full-suite runs, where earlier files seed the path.
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "examples"))
    import story_service
    story_service.clear_cache()
    yield
    story_service.clear_cache()
    # And the build subprocess (P0-2′). The worker takes its database URL per call, so it is not
    # stale across tests — this is process hygiene: without it, the first offloading test leaves a
    # child python alive for the rest of the suite. Cheap no-op when nothing offloaded (the pool is
    # created lazily).
    story_service.shutdown_build_pool()
