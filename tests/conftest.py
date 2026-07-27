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
