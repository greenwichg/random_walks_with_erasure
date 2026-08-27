"""M3 / D2 — `corpus._host_match` is O(labels), and it is the same predicate it was.

Two halves, and neither is sufficient alone:

* **Differential.** `_reference_host_match` below is the ORIGINAL expression, verbatim. Every case
  here asserts the shipped function agrees with it. A rewrite of a matching rule that is only tested
  against hand-written expectations tests the expectations, not the rewrite.
* **Structural.** The point of D2 is not "faster", it is "does not depend on how many sources are
  configured". A timing assertion would be flaky and would pass for a merely-faster scan, so the
  guard instead proves the function never **iterates** the host set: it may only ask `in`. That is
  the property that makes the cost O(labels), and it flips the moment anyone writes
  `any(... for h in hosts)` again.

Measured motivation (`docs/STORAGE_50K_DESIGN.md` §2.7): `tier_of` cost 3,428.6 µs per call against
a 50,000-host set and 0.84 µs after, because `_matches` calls this up to four times per article and
the retention path calls `tier_of` per article.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import corpus  # noqa: E402
import outlet_registry  # noqa: E402


def _reference_host_match(hosts, text):
    """The original implementation, kept verbatim as the oracle."""
    host = outlet_registry._host_of(text or "")
    return bool(host) and any(host == h or host.endswith("." + h) for h in hosts)


class _NoIterate(frozenset):
    """A host set that records how it was accessed.

    `__contains__` is the hashed lookup D2 is built on; `__iter__` is the scan it exists to remove.
    """

    def __new__(cls, items):
        self = super().__new__(cls, items)
        self.contains_calls = 0
        self.iter_calls = 0
        return self

    def __contains__(self, item):
        self.contains_calls += 1
        return super().__contains__(item)

    def __iter__(self):
        self.iter_calls += 1
        return super().__iter__()


# The awkward cases, not the easy ones: subdomains, the prefix trap the docstring names, multi-label
# public suffixes, hosts that ARE the configured entry, empty and malformed input, and URLs as well
# as bare host strings (`_matches` passes both).
_HOSTS = frozenset({"example.com", "bbc.co.uk", "a.b.example.org", "com"})

_CASES = [
    "example.com",
    "news.example.com",
    "deep.news.example.com",
    "notexample.com",                 # the prefix trap — must NOT match example.com
    "xexample.com",
    "example.com.evil.net",           # configured host as a LEFT label, must not match
    "bbc.co.uk",
    "www.bbc.co.uk",
    "bbc.co.uk.phish.net",
    "co.uk",                          # a suffix of a configured host, not itself configured
    "a.b.example.org",
    "b.example.org",                  # parent of a configured host, must not match
    "x.a.b.example.org",
    "anything.com",                   # matches the bare "com" entry, deliberately
    "",
    "   ",
    ".",
    "..",
    "example.com.",                   # trailing dot
    "https://news.example.com/a/b",
    "https://notexample.com/a/b",
    "http://WWW.BBC.CO.UK/news",      # case
    "Example.Com",
    None,
]


@pytest.mark.parametrize("text", _CASES)
def test_agrees_with_the_original_expression(text):
    assert corpus._host_match(_HOSTS, text) is bool(_reference_host_match(_HOSTS, text)), text


@pytest.mark.parametrize("text", _CASES)
def test_agrees_with_the_original_on_an_empty_host_set(text):
    empty = frozenset()
    assert corpus._host_match(empty, text) is bool(_reference_host_match(empty, text)), text


def test_the_documented_examples_hold():
    """The docstring's own claim, asserted rather than trusted."""
    hosts = frozenset({"example.com"})
    assert corpus._host_match(hosts, "news.example.com") is True
    assert corpus._host_match(hosts, "notexample.com") is False


def test_never_iterates_the_host_set():
    """The guard. Iterating is the O(sources) behaviour D2 removes; hashing is what replaces it."""
    hosts = _NoIterate({"example.com", "bbc.co.uk"})
    for text in ("deep.news.example.com", "notexample.com", "https://www.bbc.co.uk/x", ""):
        corpus._host_match(hosts, text)
    assert hosts.iter_calls == 0, (
        f"_host_match iterated the host set {hosts.iter_calls} time(s) — that is the O(sources) "
        f"scan D2 exists to remove")
    assert hosts.contains_calls > 0, "the set was never consulted at all — the test proves nothing"


def test_lookup_count_is_bounded_by_labels_not_by_set_size():
    """The worst case — a host that matches nothing — must cost the same against 2 hosts as against
    20,000.

    The non-matching case is the one to measure. A *matching* host short-circuits at whichever
    suffix hits, so its lookup count varies with where the match is rather than with set size; the
    first version of this test compared a 3-lookup hit against a 4-lookup miss and read the
    difference as scaling. The miss is also the common case in production, where almost every article
    belongs to none of the configured tiers.
    """
    host = "deep.news.absent.invalid"                 # 4 labels, in neither set
    small = _NoIterate({"example.com", "bbc.co.uk"})
    large = _NoIterate({f"source{i:05d}.example" for i in range(20_000)})
    assert corpus._host_match(small, host) is False
    assert corpus._host_match(large, host) is False
    assert small.contains_calls == large.contains_calls == len(host.split(".")), (
        f"{small.contains_calls} lookups against 2 hosts, {large.contains_calls} against 20,000, "
        f"for a {len(host.split('.'))}-label host — the cost is not bounded by labels")


def test_a_match_short_circuits_rather_than_finishing_the_walk():
    """The complement: a hit costs *fewer* lookups than the labels, and never more."""
    hosts = _NoIterate({"example.com"})
    assert corpus._host_match(hosts, "deep.news.example.com") is True
    assert hosts.contains_calls == 3, (          # deep.news.example.com -> [.., news.., example.com]
        f"expected the walk to stop at the matching suffix, took {hosts.contains_calls} lookups")


def test_resolver_agrees_with_tier_of(monkeypatch):
    """`tier_resolver` is a hoist, not a different rule."""
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "shadowed.example")
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    resolve = corpus.tier_resolver()
    for publisher, url in (("shadowed.example", "https://news.shadowed.example/a"),
                           ("tierb.example", "https://tierb.example/a"),
                           ("other.example", "https://other.example/a"),
                           (None, "https://sub.shadowed.example/a"),
                           ("shadowed.example", None),
                           (None, None)):
        assert resolve(publisher, url) == corpus.tier_of(publisher, url), (publisher, url)


def test_resolver_agrees_with_tier_of_when_nothing_is_configured(monkeypatch):
    """`tier_of` short-circuits on `enabled()`; the resolver reaches `_tier_with` against an empty
    index. Both must answer the default tier, or the hoist changes behaviour on the shipped
    configuration."""
    monkeypatch.delenv("RWE_CORPUS_SHADOW", raising=False)
    monkeypatch.delenv("RWE_CORPUS_TIER_B", raising=False)
    resolve = corpus.tier_resolver()
    for publisher, url in (("anything.example", "https://anything.example/a"), (None, None)):
        assert resolve(publisher, url) == corpus.tier_of(publisher, url) == corpus.DEFAULT_TIER


def test_resolver_reads_the_settings_once_not_per_call(monkeypatch):
    """The guard. Building the index is the linear part — ~415 µs against a 50,000-host list,
    because `os.environ` decodes a fresh string and `_index`'s memo must hash the whole value. A
    resolver that re-read per call would be `tier_of` with extra steps."""
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "shadowed.example")
    calls = {"n": 0}
    real = corpus.tier_index

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(corpus, "tier_index", counting)
    resolve = corpus.tier_resolver()
    built = calls["n"]
    for i in range(50):
        resolve(f"outlet{i}.example", f"https://outlet{i}.example/a")
    assert built == 1, f"building the resolver read the settings {built} times"
    assert calls["n"] == 1, (
        f"the settings were read {calls['n']} times across 50 resolutions — the resolver is not "
        f"hoisting anything")


def test_tier_of_is_flat_in_the_number_of_configured_sources(monkeypatch):
    """End to end through the real `tier_of`, which is where the cost was actually being paid.

    Asserted as a RATIO of lookups rather than of wall time: timing is flaky in CI and would also
    pass for a merely-faster scan, which is not what D2 promises.
    """
    seen = {}

    def counting(size, key):
        hosts = _NoIterate({f"source{i:05d}.example" for i in range(size)})
        monkeypatch.setattr(corpus, "_index", lambda setting: (frozenset(), hosts))
        corpus._index.cache_clear() if hasattr(corpus._index, "cache_clear") else None
        monkeypatch.setenv("RWE_CORPUS_SHADOW", "source00001.example")
        monkeypatch.delenv("RWE_CORPUS_TIER_B", raising=False)
        corpus.tier_of("news.source00042.example", "https://news.source00042.example/x")
        seen[key] = (hosts.contains_calls, hosts.iter_calls)

    counting(100, "small")
    counting(20_000, "large")
    assert seen["small"][1] == 0 and seen["large"][1] == 0, f"host set was iterated: {seen}"
    assert seen["small"][0] == seen["large"][0], (
        f"tier_of cost grew with the source count: {seen}")
