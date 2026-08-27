"""M3 / D2 — the retention pass resolves tiers once, not once per article.

`corpus_health._tier_age_resolver` returns an ``article -> age days`` callable that
`plan_retention` applies to every row in the catalogue. It used to call `corpus.tier_of` inside
that callable, and `tier_of` re-reads the environment every time — which is linear in the number of
configured sources, because `os.environ` hands back a freshly decoded string and `_index`'s
`lru_cache` has to hash the whole thing to find its memo.

Measured (`docs/STORAGE_50K_DESIGN.md` §2.7): against a 50,000-host list — a 999,999-byte
environment variable — `tier_of` costs ~507 µs per call and the matching alone costs 3.6 µs. Over a
7.5 M-row catalogue that is the difference between **~7 hours** and **~27 seconds**, inside the
global ingest lock.

`corpus.select` has always hoisted `tier_index()` out of its row loop. This test pins the same
property for the one call site that did not, and it is a *structural* assertion rather than a timing
one: the settings must be read once per pass, however many articles the pass resolves.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import corpus  # noqa: E402
import corpus_health  # noqa: E402
import retention_policy  # noqa: E402


@pytest.fixture()
def per_tier_policy(monkeypatch):
    """A policy with a per-tier age, which is what makes `_tier_age_resolver` return a callable."""
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "shadowed.example")
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS", "90")
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS_SHADOW", "14")
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS_TIER_B", "30")
    return retention_policy.load()


def _article(publisher, url):
    return {"scored": {"outlet": publisher}, "publisher": publisher, "canonicalUrl": url}


def test_resolver_returns_the_per_tier_ages(per_tier_policy):
    age_for = corpus_health._tier_age_resolver(per_tier_policy)
    assert age_for is not None
    assert age_for(_article("shadowed.example", "https://shadowed.example/a")) == 14
    assert age_for(_article("tierb.example", "https://tierb.example/a")) == 30
    assert age_for(_article("other.example", "https://other.example/a")) == 90


def test_subdomains_resolve_through_the_hoisted_index(per_tier_policy):
    """The hoist must not lose the subdomain tolerance `_host_match` provides."""
    age_for = corpus_health._tier_age_resolver(per_tier_policy)
    assert age_for(_article(None, "https://news.shadowed.example/a")) == 14
    assert age_for(_article(None, "https://notshadowed.example/a")) == 90


def test_no_per_tier_age_still_returns_none(monkeypatch):
    """The shipped state: a scalar age applies to everything and no resolver is built at all."""
    monkeypatch.delenv("RWE_RETENTION_MAX_AGE_DAYS_SHADOW", raising=False)
    monkeypatch.delenv("RWE_RETENTION_MAX_AGE_DAYS_TIER_B", raising=False)
    monkeypatch.setenv("RWE_RETENTION_MAX_AGE_DAYS", "90")
    assert corpus_health._tier_age_resolver(retention_policy.load()) is None


def test_settings_are_read_once_per_pass_not_once_per_article(per_tier_policy, monkeypatch):
    """The guard, and the whole point of D2's second half.

    Counting `tier_index` calls rather than measuring time: a timing assertion would be flaky, and
    would also pass for a version that read the environment per article on a *small* host list —
    which is exactly the configuration production runs today and exactly why this was not visible.
    """
    calls = {"n": 0}
    real = corpus.tier_index

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(corpus, "tier_index", counting)

    age_for = corpus_health._tier_age_resolver(per_tier_policy)
    built = calls["n"]
    for i in range(200):
        age_for(_article(f"outlet{i}.example", f"https://outlet{i}.example/a"))

    assert built <= 1, f"building the resolver read the tier settings {built} times"
    assert calls["n"] == built, (
        f"resolving 200 articles read the tier settings {calls['n'] - built} extra time(s) — the "
        f"pass is still paying the environment read per article, which is the O(sources) cost D2 "
        f"exists to remove")
