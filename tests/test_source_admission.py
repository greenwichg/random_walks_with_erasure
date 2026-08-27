"""M11 — source admission is durable, resumable data.

The two load-bearing tests are `test_a_second_full_campaign_makes_no_requests` (duplicate-run
idempotence) and `test_an_interrupted_campaign_resumes_where_it_stopped` (interruption/resume).
Everything else exists because one of them could pass for the wrong reason.

## Every test here was verified by breaking the product

The recurring defect in this repository's own instruments is a test whose premise is switched off —
by the harness, by an environment default, or by a guard that cannot fire. Every invariant below was
checked by reverting the specific product line it is about and confirming a test fails. **Thirteen
mutations, thirteen caught:**

    COMPLETED made empty                        the probing claim made a no-op
    re-seeding downgrades the state             record_admission_probe refuses nothing
    check_admission_tier never raises           withdrawal clears `tier`
    corpus.enabled ignores admissions           tier_index REPLACES the env list
    admitted_configs skips the is_shadow check  the runner catches BaseException
    INCOMPLETE maps to `rejected`               an incomplete probe gets no cooloff
    _lifecycle_identity drifts from M8's

The reproduction script is `mutate.py` in the session scratchpad; each entry is a one-line
substitution against the anchor named in the left column.

## Why `_wired` is a fixture and not a line in each test

`corpus.wire_admissions` sets a module-level global. A test that wires a store and does not unwire
leaves the rest of the session pointed at a deleted tmp database — the exact cross-test coupling
that the explicit-wiring design exists to avoid, re-introduced by the tests for it.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import corpus  # noqa: E402
import crawler  # noqa: E402
import source_admission as sa  # noqa: E402
import source_campaign as sc  # noqa: E402
import source_validation as sv  # noqa: E402
import store as store_mod  # noqa: E402

_NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _cand(host, articles=50, *, language="en", publisher=None, eligible=True):
    return {"host": host, "articles": articles, "language": language,
            "publishers": [publisher] if publisher else [host], "eligible": eligible}


@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path / 'adm.db'}")


@pytest.fixture()
def _wired(st):
    """Wire the admission table into `corpus` and **always** unwire. See the module docstring."""
    corpus.wire_admissions(st.admitted_shadow_hosts)
    try:
        yield st
    finally:
        corpus.wire_admissions(None)


@pytest.fixture()
def seeded(st):
    st.record_admission_candidates(
        [_cand("alpha.example", 90), _cand("beta.example", 80), _cand("gamma.example", 70),
         _cand("delta.example", 60), _cand("small.example", 3, eligible=False)])
    return st


# --------------------------------------------------------------------------- seeding
def test_reseeding_is_a_no_op_for_hosts_already_known(seeded):
    """Discovery runs over a catalogue that keeps growing, so it re-offers the same hosts every
    time. If seeding were not idempotent, every pass would refill the probe queue."""
    again = seeded.record_admission_candidates(
        [_cand("alpha.example", 90), _cand("beta.example", 80), _cand("gamma.example", 70),
         _cand("delta.example", 60)])
    assert again == {"inserted": 0, "refreshed": 0, "unchanged": 4, "skipped": 0}


def test_seeding_refreshes_evidence_but_never_downgrades_state(seeded):
    """The article count grows and `language` fills in as `parse_feed` backfills it — that is what
    discovery is for. The *state* is a different fact and re-seeding must not touch it."""
    seeded.claim_admission_probe("alpha.example")
    seeded.record_admission_probe("alpha.example", verdict="REJECT", requests=1)

    counts = seeded.record_admission_candidates([_cand("alpha.example", 300, language="fr")])
    row = seeded.admission_row("alpha.example")
    assert counts["refreshed"] == 1
    assert row["articles"] == 300 and row["language"] == "fr"
    assert row["state"] == "rejected", "a re-seed put a rejected host back in the probe queue"
    assert row["probeCount"] == 1 and row["requestsSpent"] == 1, "seeding reset probe accounting"


def test_a_host_the_offline_gates_reject_is_never_seeded(seeded):
    """`source_discovery` already decided against it. A row in the queue is a request waiting to be
    made, and this one is not justified."""
    assert seeded.admission_row("small.example") is None
    assert set(r["host"] for r in seeded.admission_rows()) == {
        "alpha.example", "beta.example", "gamma.example", "delta.example"}


# --------------------------------------------------------------------------- the claim
def test_a_claim_locks_the_host_against_a_concurrent_campaign(seeded):
    """`crawler.RateLimiter` lives inside one process. Two campaigns would each believe they were
    being polite while the publisher saw double; the claim is the only shared record of what is in
    flight."""
    first = seeded.claim_admission_probe("alpha.example")
    assert first["state"] == "probing"
    assert seeded.claim_admission_probe("alpha.example") is None
    assert "in-flight window" in seeded.admission_skip_reason("alpha.example")


def test_a_stale_claim_is_reclaimable_because_the_process_that_made_it_is_presumed_dead(seeded):
    seeded.claim_admission_probe("alpha.example",
                                 at=(_NOW - timedelta(hours=2)).isoformat())
    assert seeded.claim_admission_probe("alpha.example", at=_NOW.isoformat()) is not None


def test_a_host_that_is_not_a_candidate_is_never_probed_even_forced(seeded):
    """The bound that keeps a campaign inside the discovered candidate set rather than turning into
    a crawl of the internet."""
    assert seeded.claim_admission_probe("stranger.example", force=True) is None
    assert "not a candidate" in seeded.admission_skip_reason("stranger.example", force=True)


def test_a_verdict_cannot_be_recorded_without_a_claim(seeded):
    """Claim and record are a strict pair, so `probe_count` counts requests made rather than calls
    to this method."""
    with pytest.raises(ValueError, match="not 'probing'"):
        seeded.record_admission_probe("alpha.example", verdict="ADMIT")
    seeded.claim_admission_probe("alpha.example")
    seeded.record_admission_probe("alpha.example", verdict="ADMIT")
    with pytest.raises(ValueError, match="not 'probing'"):
        seeded.record_admission_probe("alpha.example", verdict="ADMIT")
    assert seeded.admission_row("alpha.example")["probeCount"] == 1


# --------------------------------------------------------------------------- never re-probed
@pytest.mark.parametrize("verdict,state", [("ADMIT", "validated"), ("REJECT", "rejected")])
def test_an_answered_host_is_not_probed_again(seeded, verdict, state):
    seeded.claim_admission_probe("alpha.example")
    seeded.record_admission_probe("alpha.example", verdict=verdict, requests=3)
    assert seeded.admission_row("alpha.example")["state"] == state
    assert seeded.claim_admission_probe("alpha.example") is None
    assert "a completed host is not re-probed" in seeded.admission_skip_reason("alpha.example")


def test_a_rejection_is_never_retried_on_a_timer(seeded):
    """Re-asking a publisher who refused us is how a discovery pipeline becomes a nuisance.
    `reopen` is the way back, and it has a name and a reason field so the record says a human
    decided rather than that a clock expired."""
    seeded.claim_admission_probe("alpha.example")
    row = seeded.record_admission_probe("alpha.example", verdict="REJECT", requests=1)
    assert row["retryAfter"] is None
    far_future = (_NOW + timedelta(days=3650)).isoformat()
    assert seeded.claim_admission_probe("alpha.example", at=far_future) is None

    reopened = seeded.reopen_admission("alpha.example", reason="they added a feed")
    assert reopened["state"] == "candidate"
    assert reopened["probeCount"] == 1, "reopening reset the count of how often we have knocked"
    assert seeded.claim_admission_probe("alpha.example") is not None


def test_an_incomplete_probe_is_retried_but_only_after_a_cooloff(seeded):
    """INCOMPLETE is OUR failure, not the publisher's. Folding it into `rejected` would record a
    refusal that never happened and make it permanent."""
    seeded.claim_admission_probe("alpha.example", at=_NOW.isoformat())
    row = seeded.record_admission_probe("alpha.example", verdict="INCOMPLETE", requests=1,
                                        at=_NOW.isoformat())
    assert row["state"] == "incomplete" and row["retryAfter"]
    soon = (_NOW + timedelta(hours=sa.INCOMPLETE_RETRY_HOURS / 2)).isoformat()
    assert seeded.claim_admission_probe("alpha.example", at=soon) is None
    later = (_NOW + timedelta(hours=sa.INCOMPLETE_RETRY_HOURS + 1)).isoformat()
    assert seeded.claim_admission_probe("alpha.example", at=later) is not None


def test_an_unknown_verdict_raises_rather_than_defaulting(seeded):
    """A fourth verdict appearing in `source_validation` and silently landing in `incomplete` —
    retryable, forever — is the quiet mapping this repository keeps having to correct."""
    seeded.claim_admission_probe("alpha.example")
    with pytest.raises(ValueError, match="unknown validation verdict"):
        seeded.record_admission_probe("alpha.example", verdict="MAYBE")


# --------------------------------------------------------------------------- the campaign
class _Probe:
    """A fake `source_validation.validate` that counts calls and can fail at a chosen host."""

    def __init__(self, verdicts=None, *, boom_at=None, exc=KeyboardInterrupt):
        self.verdicts, self.boom_at, self.exc = verdicts or {}, boom_at, exc
        self.hosts: list = []

    def __call__(self, cand, **kw):
        host = cand["host"]
        if host == self.boom_at:
            raise self.exc(f"interrupted at {host}")
        self.hosts.append(host)
        verdict = self.verdicts.get(host, "ADMIT")
        return {"host": host, "verdict": verdict, "requests": 3,
                "feed": f"https://{host}/feed", "discoveredVia": "feed",
                "samples": [f"https://{host}/a/1"], "sitemaps": [],
                "gates": [sv.Gate(1, "robots.txt permits our agent", sv.PASS, "allowed")]}


def _run(db, *argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sc.main([*argv, "--db", db])
    return buf.getvalue()


@pytest.fixture()
def campaign(tmp_path, monkeypatch):
    """A seeded four-host table and its db URL, with `corpus` unwired afterwards.

    The store is disposed so the CLI opens its own connection, which is what a real campaign does
    and what makes the `probing` row a genuinely cross-process fact rather than a shared object."""
    db = f"sqlite:///{tmp_path / 'campaign.db'}"
    st = store_mod.Store(db)
    st.record_admission_candidates([_cand("alpha.example", 90), _cand("beta.example", 80),
                                    _cand("gamma.example", 70), _cand("delta.example", 60)])
    st.engine.dispose()
    try:
        yield db
    finally:
        corpus.wire_admissions(None)


def test_a_second_full_campaign_makes_no_requests(campaign, monkeypatch):
    """**Duplicate-run idempotence.** Every host the first run touched is completed, so the second
    run's queue is empty and `probe_count` stays at 1 everywhere.

    Asserting "the second run printed fewer lines" would pass for any change that printed less.
    This asserts the number of times `validate` was called and the per-host probe count, which is
    the thing the requirement is about."""
    probe = _Probe({"gamma.example": "REJECT"})
    monkeypatch.setattr(sc.sv, "validate", probe)

    _run(campaign, "probe", "--interval", "0")
    assert sorted(probe.hosts) == ["alpha.example", "beta.example", "delta.example",
                                   "gamma.example"]

    probe.hosts.clear()
    out = _run(campaign, "probe", "--interval", "0")
    assert probe.hosts == [], f"a second campaign re-probed {probe.hosts}"
    assert "PROBING 0 HOSTS" in out

    st = store_mod.Store(campaign)
    assert {r["host"]: r["probeCount"] for r in st.admission_rows()} == {
        "alpha.example": 1, "beta.example": 1, "gamma.example": 1, "delta.example": 1}
    assert st.admission_census()["requests"] == 12, "requests are counted once, not twice"


def test_an_interrupted_campaign_resumes_where_it_stopped(campaign, monkeypatch):
    """**Interruption/resume.** `KeyboardInterrupt` is a BaseException and the runner deliberately
    does not catch it, so the row stays `probing` — a real kill, not a simulated verdict.

    The resumed run must not re-ask the publishers already answered, and must reach the ones it
    never got to. Both halves are asserted: re-probing the answered hosts is the cost this milestone
    exists to remove, and skipping the unanswered ones would make the campaign silently incomplete.
    """
    order = [r["host"] for r in store_mod.Store(campaign).admission_rows()]
    assert order == ["alpha.example", "beta.example", "gamma.example", "delta.example"], order

    first = _Probe(boom_at="gamma.example")
    monkeypatch.setattr(sc.sv, "validate", first)
    with pytest.raises(KeyboardInterrupt):
        _run(campaign, "probe", "--interval", "0")
    assert first.hosts == ["alpha.example", "beta.example"]

    st = store_mod.Store(campaign)
    assert st.admission_row("gamma.example")["state"] == "probing", \
        "the interrupted host is indistinguishable from one never touched"
    assert st.admission_row("delta.example")["state"] == "candidate"
    st.engine.dispose()

    second = _Probe()
    monkeypatch.setattr(sc.sv, "validate", second)
    _run(campaign, "probe", "--interval", "0", "--stale-minutes", "0")
    assert second.hosts == ["gamma.example", "delta.example"], (
        f"resume re-asked publishers who had already answered, or missed one: {second.hosts}")

    st = store_mod.Store(campaign)
    assert {r["host"]: r["probeCount"] for r in st.admission_rows()} == {
        "alpha.example": 1, "beta.example": 1, "gamma.example": 1, "delta.example": 1}


def test_the_interrupted_host_is_held_back_while_its_claim_could_still_be_live(campaign,
                                                                              monkeypatch):
    """There is no way to tell a crashed run from a live one without a liveness channel, so the
    default errs toward not hitting one publisher from two campaigns at once. `status` says so out
    loud rather than leaving the host to look finished."""
    monkeypatch.setattr(sc.sv, "validate", _Probe(boom_at="gamma.example"))
    with pytest.raises(KeyboardInterrupt):
        _run(campaign, "probe", "--interval", "0")

    resumed = _Probe()
    monkeypatch.setattr(sc.sv, "validate", resumed)
    _run(campaign, "probe", "--interval", "0")          # default --stale-minutes
    assert resumed.hosts == ["delta.example"], resumed.hosts

    out = _run(campaign, "status")
    assert "in-flight window" in out and "gamma.example" in out


def test_a_transport_failure_is_recorded_as_incomplete_not_as_a_rejection(campaign, monkeypatch):
    """A timeout is our failure. Recording it as REJECT would claim a publisher refused us, and
    would make that claim permanent — a rejection is never retried."""
    monkeypatch.setattr(sc.sv, "validate",
                        _Probe(boom_at="beta.example", exc=TimeoutError))
    _run(campaign, "probe", "--interval", "0")
    st = store_mod.Store(campaign)
    row = st.admission_row("beta.example")
    assert row["state"] == "incomplete" and row["verdict"] == "INCOMPLETE"
    assert row["retryAfter"], "an incomplete probe with no retry time is a permanent rejection"
    assert any("TimeoutError" in (g.get("detail") or "") for g in row["gates"])


def test_the_probe_still_uses_the_shared_robots_policy_and_rate_limiter(campaign, monkeypatch):
    """M11 changes WHICH hosts are asked, never HOW. A campaign that built its own robots parser or
    dropped the limiter would be a fifth drifted definition of a policy this repo has already had to
    converge four times."""
    seen: dict = {}

    def _capture(cand, **kw):
        seen.update(kw)
        return _Probe()(cand)

    monkeypatch.setattr(sc.sv, "validate", _capture)
    _run(campaign, "probe", "--interval", "0.5", "--limit", "1")
    assert seen["fetch"] is crawler._fetch_text
    assert isinstance(seen["robots"], crawler.RobotsPolicy)
    assert isinstance(seen["limiter"], crawler.RateLimiter)
    assert seen["limiter"].default_interval == 0.5


def test_a_robots_refusal_reaches_the_table_as_a_rejection(campaign, monkeypatch):
    """End to end through the REAL `source_validation.validate`, with only the socket faked, so the
    fail-closed robots gate is what decides — not a stubbed verdict."""
    def _fetch(url):
        if url.endswith("/robots.txt"):
            return "User-agent: *\nDisallow: /\n"
        raise AssertionError(f"nothing should be fetched after a refusal: {url}")

    monkeypatch.setattr(crawler, "_fetch_text", _fetch)
    _run(campaign, "probe", "--interval", "0", "--limit", "1")
    row = store_mod.Store(campaign).admission_row("alpha.example")
    assert row["state"] == "rejected"
    gate1 = next(g for g in row["gates"] if g["number"] == 1)
    assert gate1["status"] == sv.FAIL


def test_dry_run_makes_no_request_and_writes_no_claim(campaign, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("--dry-run made a request")

    monkeypatch.setattr(sc.sv, "validate", _boom)
    out = _run(campaign, "probe", "--dry-run")
    assert "would probe" in out
    assert all(r["state"] == "candidate" for r in store_mod.Store(campaign).admission_rows())


# --------------------------------------------------------------------------- admission & tier
def test_admission_assigns_the_shadow_tier_and_refuses_every_other(seeded):
    """`corpus.DEFAULT_TIER` is "A", so the difference between "admitted into the shadow lane" and
    "admitted into the clustering corpus" is one string. Guarded at the policy AND at the write."""
    seeded.claim_admission_probe("alpha.example")
    seeded.record_admission_probe("alpha.example", verdict="ADMIT",
                                  feed_url="https://alpha.example/feed", discovered_via="feed")
    for tier in ("A", "B", "a", ""):
        with pytest.raises(ValueError, match="may only assign"):
            seeded.admit_source("alpha.example", tier=tier)
    with pytest.raises(ValueError, match="may only assign"):
        sa.check_admission_tier("A")
    assert seeded.admit_source("alpha.example")["tier"] == "shadow"


def test_only_a_validated_host_is_admitted(seeded):
    seeded.claim_admission_probe("alpha.example")
    seeded.record_admission_probe("alpha.example", verdict="REJECT")
    with pytest.raises(ValueError, match="not 'validated'"):
        seeded.admit_source("alpha.example")
    assert seeded.admit_source("alpha.example", force=True)["state"] == "admitted"


def test_an_admitted_host_is_shadow_to_corpus_and_its_subdomains_with_it(_wired):
    """The whole point of the table: an admission has to reach the tier resolver, or the source is
    crawled straight into Tier A by omission."""
    st = _wired
    st.record_admission_candidates([_cand("alpha.example")])
    st.claim_admission_probe("alpha.example")
    st.record_admission_probe("alpha.example", verdict="ADMIT", feed_url="https://alpha.example/f")

    assert corpus.tier_of("alpha.example", "https://alpha.example/a/1") == "A", \
        "validated is not admitted — nothing should be serving yet"

    st.admit_source("alpha.example")
    corpus.wire_admissions(st.admitted_shadow_hosts)     # drops the snapshot; see wire_admissions
    assert corpus.enabled(), "the tier filter short-circuits, so tier_of returns the A default"
    assert corpus.tier_of("alpha.example", "https://alpha.example/a/1") == "shadow"
    assert corpus.tier_of("news.alpha.example", "https://news.alpha.example/a/1") == "shadow"
    assert corpus.tier_of("notalpha.example", "https://notalpha.example/a/1") == "A"
    assert corpus.is_shadow("alpha.example", "https://alpha.example/a/1")
    assert "alpha.example" in corpus.shadow_exclusions(), \
        "an admitted source would be searchable — stored, not clustered, and surfaced anyway"


def test_the_table_is_unioned_with_the_environment_never_replacing_it(_wired, monkeypatch):
    """With `DEFAULT_TIER == "A"`, a table that REPLACED the env lists would put the whole corpus
    into the clustering tier the moment a read came back empty — and it would look like an
    improvement rather than an error."""
    st = _wired
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "envonly.example")
    st.record_admission_candidates([_cand("alpha.example")])
    st.claim_admission_probe("alpha.example")
    st.record_admission_probe("alpha.example", verdict="ADMIT")
    st.admit_source("alpha.example")
    corpus.wire_admissions(st.admitted_shadow_hosts)

    assert corpus.shadow_exclusions() == {"envonly.example", "alpha.example"}
    assert corpus.tier_of("envonly.example", "https://envonly.example/x") == "shadow"
    assert corpus.tier_of("alpha.example", "https://alpha.example/x") == "shadow"


def test_a_provider_that_raises_degrades_to_the_environment_alone(monkeypatch):
    """The tier filter must not be able to take the API down, and its failure direction must be
    today's shipped state rather than a novel one."""
    def _boom():
        raise RuntimeError("database is locked")

    monkeypatch.setenv("RWE_CORPUS_SHADOW", "envonly.example")
    corpus.wire_admissions(_boom)
    try:
        assert corpus.admitted_shadow_hosts(refresh=True) == frozenset()
        assert corpus.tier_of("envonly.example", "https://envonly.example/x") == "shadow"
    finally:
        corpus.wire_admissions(None)


def test_nothing_wired_is_free_and_byte_identical(monkeypatch):
    monkeypatch.delenv("RWE_CORPUS_SHADOW", raising=False)
    monkeypatch.delenv("RWE_CORPUS_TIER_B", raising=False)
    corpus.wire_admissions(None)
    assert corpus.admitted_shadow_hosts() == frozenset()
    assert corpus.enabled() is False
    rows = [{"publisher": "x", "canonicalUrl": "https://x.example/1", "publishedAt": "2026-01-01"}]
    assert corpus.select(rows, log=lambda *a, **k: None) is rows


def test_withdrawal_stops_the_crawl_and_keeps_the_shadow_assignment(_wired):
    """Clearing the tier would take every article already ingested and put it in the clustering
    corpus — an operator reducing a source's reach would be promoting it."""
    st = _wired
    st.record_admission_candidates([_cand("alpha.example")])
    st.claim_admission_probe("alpha.example")
    st.record_admission_probe("alpha.example", verdict="ADMIT", feed_url="https://alpha.example/f")
    st.admit_source("alpha.example")
    assert [r["host"] for r in st.admitted_crawl_rows()] == ["alpha.example"]
    with pytest.raises(ValueError, match="withdraw it before reopening"):
        st.reopen_admission("alpha.example")            # reopening something that is SERVING

    row = st.withdraw_source("alpha.example", reason="too much syndication")
    assert row["state"] == "withdrawn"
    assert row["tier"] == "shadow", "withdrawal dropped the shadow assignment"
    assert st.admitted_crawl_rows() == [], "a withdrawn source is still crawled"
    corpus.wire_admissions(st.admitted_shadow_hosts)
    assert corpus.tier_of("alpha.example", "https://alpha.example/a/1") == "shadow"

    # Reconsidering it later puts it back in the probe queue and STILL keeps the tier: every path
    # out of `admitted` leaves the catalogue rows where they are.
    assert st.reopen_admission("alpha.example")["tier"] == "shadow"
    corpus.wire_admissions(st.admitted_shadow_hosts)
    assert corpus.tier_of("alpha.example", "https://alpha.example/a/1") == "shadow"


@pytest.mark.parametrize("publisher", [
    "BBC", "bbc", "The Guardian", "Beta News", "BETA NEWS", "alpha.example",
    "News.Google.Com", "", "  Reuters  "])
def test_the_lifecycle_key_agrees_with_the_one_m8_writes_under(publisher):
    """Differential, against the real function rather than a restatement of the rule.

    If these disagree, an admission writes a `SourceLifecycle` row under one key and M8 writes its
    evidence under another: the source looks un-evaluated forever while two rows describe it.
    Restating the expression here would make it the third copy — `audit_source_cohort` and
    `audit_shadow_cohort` are already two — and a third copy cannot detect drift in the other two."""
    import audit_shadow_cohort as asc
    import outlet_registry
    reg = outlet_registry.default_registry()
    assert store_mod.Store._lifecycle_identity(publisher) == \
        asc._identity(reg, {"publisher": publisher})


def test_admission_records_the_shadow_transition_in_the_lifecycle_ledger(seeded):
    """The one arrow between the two tables. M8 looks for sources in `SourceLifecycle`; an admission
    that did not appear there would be a source nothing ever evaluates."""
    seeded.claim_admission_probe("alpha.example")
    seeded.record_admission_probe("alpha.example", verdict="ADMIT",
                                  feed_url="https://alpha.example/f", requests=3)
    seeded.admit_source("alpha.example", reason="passed every gate")

    assert seeded.source_lifecycle("alpha.example")["state"] == "shadow"
    event = seeded.source_lifecycle_events("alpha.example")[0]
    assert event["to"] == "shadow" and event["applied"] is True and event["automatic"] is False
    assert event["evidence"]["host"] == "alpha.example"
    assert event["evidence"]["requestsSpent"] == 3


# --------------------------------------------------------------------------- crawl config
def _admitted(st, host, feed, via="feed", pattern=None, publisher=None):
    st.record_admission_candidates([_cand(host, publisher=publisher)])
    st.claim_admission_probe(host)
    st.record_admission_probe(host, verdict="ADMIT", feed_url=feed, discovered_via=via)
    return st.admit_source(host, publisher=publisher, article_pattern=pattern)


def test_an_admitted_source_becomes_a_crawl_config_without_a_deploy(_wired):
    """`crawler_publishers.json` is baked into the image, so before M11 admitting a source was a
    code change. Eight publishers is fine that way; 1,173 candidates is not."""
    st = _wired
    _admitted(st, "alpha.example", "https://alpha.example/feed")
    corpus.wire_admissions(st.admitted_shadow_hosts)

    cfg = next(c for c in crawler.admitted_configs(st) if c.publisher == "alpha.example")
    assert cfg.domains == ("alpha.example",), "the security boundary widened"
    assert [(s.kind, s.url) for s in cfg.sources] == [("rss", "https://alpha.example/feed")]
    assert cfg.max_age_days == sa.ADMITTED_MAX_AGE_DAYS, (
        "an archive sitemap and a news sitemap are the same file format; SCMP's returned 19,962 "
        "URLs spanning years")
    assert cfg.article_pattern == "", "a GUESSED pattern is worse than none — it ingests nothing"
    assert cfg.enabled is True


def test_a_news_sitemap_is_admitted_as_a_sitemap_rung_not_as_a_feed(_wired):
    """Putting a `<urlset>` in `RWE_RSS_FEEDS` ingests NOTHING: it has no `<channel>` and no
    `<item>`, so it parses to zero entries and reports healthy forever."""
    st = _wired
    row = _admitted(st, "kait.example", "https://kait.example/news-sitemap.xml", via="news sitemap")
    assert sa.crawl_config_fields(row)["sources"][0]["kind"] == "sitemap"


def test_a_feed_on_another_host_is_a_discovery_domain_not_an_article_domain(_wired):
    """The BBC serves feeds from `bbci.co.uk` and journalism from `bbc.co.uk`. Folding the feed host
    into `domains` would widen the set of hosts allowed to yield ARTICLES."""
    st = _wired
    row = _admitted(st, "trib.example", "https://feeds.other.example/trib.xml")
    fields = sa.crawl_config_fields(row)
    assert fields["domains"] == ("trib.example",)
    assert fields["discovery_domains"] == ("feeds.other.example",)


def test_a_subdomain_feed_needs_no_discovery_domain(_wired):
    st = _wired
    row = _admitted(st, "trib.example", "https://rss.trib.example/all.xml")
    assert "discovery_domains" not in sa.crawl_config_fields(row)


def test_an_unregistered_outlet_is_published_under_its_host(_wired):
    """Discovering unregistered outlets is the entire point, so the registry usually has no row.
    `ingest.Scorer._resolve_outlet` already falls back to the URL's domain, and `corpus._matches`
    tests the host set against the publisher STRING — so a host-named publisher is the shape both
    halves already handle."""
    st = _wired
    row = _admitted(st, "alpha.example", "https://alpha.example/f")
    assert sa.crawl_config_fields(row)["publisher"] == "alpha.example"


def test_the_json_config_wins_over_a_table_row_for_the_same_publisher(_wired, tmp_path):
    """Those eight were verified against the live sites by hand. A table row must not silently
    replace a checked `article_pattern` with an empty one."""
    st = _wired
    _admitted(st, "bbc.example", "https://bbc.example/f", publisher="BBC")
    corpus.wire_admissions(st.admitted_shadow_hosts)

    path = tmp_path / "pubs.json"
    path.write_text(json.dumps({"publishers": [
        {"publisher": "BBC", "domains": ["bbc.example"], "article_pattern": "/news/",
         "sources": [{"kind": "rss", "url": "https://bbc.example/verified"}]}]}))

    configs = crawler.load_config(str(path), store_=st)
    assert [c.publisher for c in configs] == ["BBC"]
    assert configs[0].article_pattern == "/news/"


def test_load_config_without_a_store_is_unchanged(_wired):
    st = _wired
    _admitted(st, "alpha.example", "https://alpha.example/f")
    corpus.wire_admissions(st.admitted_shadow_hosts)
    assert "alpha.example" not in {c.publisher for c in crawler.load_config()}
    assert "alpha.example" in {c.publisher for c in crawler.load_config(store_=st)}


def test_the_crawl_set_is_always_a_subset_of_what_corpus_calls_shadow(_wired, monkeypatch):
    """`CrawlAdapter.in_shadow` reads `corpus`, which caches for a minute. Filtering the config list
    through the same predicate means cache skew can only ever REMOVE a source from the crawl, never
    add one that is not shadowed — the fail-safe direction `DEFAULT_TIER == "A"` demands."""
    st = _wired
    _admitted(st, "alpha.example", "https://alpha.example/f")
    corpus.wire_admissions(lambda: frozenset())          # a corpus that has not caught up
    assert crawler.admitted_configs(st) == []
    corpus.wire_admissions(st.admitted_shadow_hosts)
    assert [c.publisher for c in crawler.admitted_configs(st)] == ["alpha.example"]


def test_a_crawl_adapter_from_the_table_still_needs_both_switches(_wired, monkeypatch):
    """`RWE_CRAWL_ENABLED` defaults to OFF and shadow membership is a hard precondition. Neither is
    relaxed by the source having come from the table."""
    st = _wired
    _admitted(st, "alpha.example", "https://alpha.example/f")
    corpus.wire_admissions(st.admitted_shadow_hosts)
    cfg = crawler.admitted_configs(st)[0]
    adapter = crawler.CrawlAdapter(cfg)

    monkeypatch.delenv("RWE_CRAWL_ENABLED", raising=False)
    assert adapter.enabled() is False
    monkeypatch.setenv("RWE_CRAWL_ENABLED", "1")
    assert adapter.in_shadow() is True and adapter.enabled() is True
    corpus.wire_admissions(lambda: frozenset())
    assert adapter.in_shadow() is False and adapter.enabled() is False


# --------------------------------------------------------------------------- the CLI
def test_seed_probe_admit_end_to_end(campaign, monkeypatch):
    monkeypatch.setattr(sc.sv, "validate", _Probe({"gamma.example": "REJECT"}))
    _run(campaign, "probe", "--interval", "0")
    out = _run(campaign, "admit", "--all-validated")
    assert "ADMITTING 3 HOST(S) TO THE shadow LANE" in out
    assert "3 admitted, 0 refused" in out

    st = store_mod.Store(campaign)
    assert st.admission_census()["admitted"] == 3
    assert st.admitted_shadow_hosts() == {"alpha.example", "beta.example", "delta.example"}


def test_admit_refuses_to_do_nothing_by_default(campaign):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = sc.main(["admit", "--db", campaign])
    assert rc == 2 and "Refusing to admit nothing" in out.getvalue()


def test_status_reports_what_has_been_spent_on_publishers(campaign, monkeypatch):
    monkeypatch.setattr(sc.sv, "validate", _Probe())
    _run(campaign, "probe", "--interval", "0", "--limit", "2")
    out = _run(campaign, "status")
    assert "requests spent on publishers: 6" in out
    assert "2 host(s) would be probed" in out


def test_emit_config_reports_the_table_as_the_configuration_it_replaces(campaign, monkeypatch):
    monkeypatch.setattr(sc.sv, "validate", _Probe())
    _run(campaign, "probe", "--interval", "0", "--limit", "1")
    _run(campaign, "admit", "--hosts", "alpha.example")
    out = _run(campaign, "emit-config")
    assert "RWE_CORPUS_SHADOW_FROM_TABLE=alpha.example" in out
    body = json.loads(out[out.index("{"):])
    assert body["publishers"][0]["domains"] == ["alpha.example"]
    assert body["publishers"][0]["max_age_days"] == sa.ADMITTED_MAX_AGE_DAYS
