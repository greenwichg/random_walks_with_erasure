"""source_admission.py — M11: the admission state machine, as policy.

**M11 of `docs/SCALE_ROADMAP.md`.** Pure: no store, no network, no environment, no writes. It takes
one admission row and says what may happen to it next. `store.SourceAdmission` is where the rows
live; `source_campaign.py` is the runner that moves them.

## What was ephemeral, and what that cost

M10 raised the candidate pool from 177 to **1,173 hosts** — 3,519 requests at three per host, about
two hours of polite crawling. At that size the pipeline's admission state was still, in its entirety:

* `audit_source_discovery.py --probe` printing verdicts to stdout, plus an optional `--json` dump
  **that nothing ever reads back**;
* `examples/data/crawler_publishers.json`, baked into the image — admitting a source is a code
  change and a deploy;
* `RWE_CORPUS_SHADOW` / `RWE_CORPUS_TIER_B`, environment strings in `deploy/.env`.

Three consequences, all of which bind at 1,173 and none of which bound at 5:

1. **A campaign cannot be resumed.** Interrupt at host 300 and the next run starts at host 1, paying
   900 requests to re-ask 300 publishers a question they have already answered. `--limit N` takes the
   top N *by article count* — a stable prefix, so running it twice probes the same hosts twice. There
   is no "next N". `--hosts` is the only way to advance, and it requires a human to keep the ledger
   of what has been done outside the system.
2. **A rejection is not remembered.** Nothing stops the next run re-probing a host whose robots.txt
   refused us last week. Politeness is not only an interval between requests; it is also not asking
   again once you have been told no.
3. **`crawler.RateLimiter` is per process.** Two campaigns running at once each believe they are
   being polite, and the publisher sees double. Nothing in the current design can detect that,
   because there is no shared record of what is in flight.

The table fixes all three with one mechanism: **per-host state**. Resume is then a set difference
rather than an offset — which matters, because the candidate ordering is *not* stable between runs
(it is by article count, and ingestion continues), so "skip the first k" would silently skip the
wrong k.

## The states

``candidate``   discovery found it, it cleared the offline gates, no request has been made.
``probing``     a probe has been **claimed** and has not reported. The crash marker, and the
                cross-process lock (see :func:`may_probe`).
``validated``   verdict ``ADMIT`` — every gate passed. **Not yet admitted**: nothing is serving.
``rejected``    verdict ``REJECT`` — a gate FAILED. The publisher, or the evidence, said no.
``incomplete``  verdict ``INCOMPLETE`` — some gate is ``UNKNOWN``. **Our** failure, not theirs.
``admitted``    wired into the shadow lane: a crawl config and a shadow tier assignment exist.
``withdrawn``   was admitted, taken back out. Stops crawling; **stays shadowed** (see below).

### Why ``probing`` and ``incomplete`` exist, given the milestone named four states

`candidate → validated → rejected / admitted` is the shape of the decision. These two are the shape
of the *failure*, and leaving them out would have made both failures indistinguishable from success:

* Without ``probing``, a process killed between "request sent" and "verdict written" leaves the host
  looking untouched. The next run cannot tell an interrupted host from a fresh one, and two
  concurrent runs cannot tell that a host is already in flight.
* Without ``incomplete``, `source_validation`'s third verdict has nowhere to go. Folding it into
  ``rejected`` would record a publisher as having refused us when in fact our own network failed —
  and would make the mistake permanent, because a rejection is never retried. That is the mirror of
  the defect `source_validation` was built around: *a gate that cannot fire reading as a gate that
  passed*. A gate that could not be **asked** must not read as a gate that **failed** either.

### Why ``withdrawn`` keeps its shadow assignment

`corpus.DEFAULT_TIER` is ``"A"``. An outlet nothing says anything about is in the clustering corpus.
So "un-admit this source" must not mean "delete its tier row" — that would take every article we
already ingested from it and put them straight into Tier A, which is *promotion by omission* and
precisely what `crawler.CrawlAdapter.in_shadow` exists to prevent.

Withdrawal therefore does one thing: it stops the crawl. The shadow assignment stays, so the rows
already in the catalogue stay out of the story builder. Moving them anywhere else is M9's decision,
with M9's evidence and M9's counterfactual.

## What this module does NOT do

It does not promote to Tier A, and there is no code path here that could. :data:`ADMISSION_TIERS` is
``("shadow", "B")`` and :func:`check_admission_tier` refuses anything else — a guard at the policy,
mirrored by a guard at the write in `store.admit_source`, because M9's own docstring records that "M9
automates the decision and emits the configuration; it never mutates serving state" and admission is
the first thing in this pipeline that *does* mutate serving state. It is allowed to, in exactly one
direction: into a lane the story builder cannot see.

## Why there are two such lanes and not one

``shadow`` is where an *unevaluated* source belongs — surfaced nowhere, watched rather than
published. That was the only destination until the 50,000-outlet arithmetic made it insufficient:
the target is ~5,000 Tier A and ~45,000 Tier B, so a pipeline whose only durable outcome was
"hide it from readers" could reach a twentieth of the goal at best. ``B`` is the other lane the
builder cannot see, and the one that is *searchable* — which is what most of a breadth corpus is.

Both are still partition changes on live rows, because every candidate is a host we already ingest
and `corpus.DEFAULT_TIER` is ``"A"``. `store.admit_source` refuses either without
``accept_partition_change``, and states the per-tier consequence in the refusal rather than a
generic one.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

#: Every admission state. Ordered by where a host is in the pipeline, not by privilege.
STATES = ("candidate", "probing", "validated", "rejected", "incomplete", "admitted", "withdrawn")

#: States a probe may move a host out of. Everything else is finished with the network.
PROBEABLE = frozenset({"candidate", "probing", "incomplete"})

#: States that mean "this host has been answered". Never re-probed without an explicit ``force``.
#: This frozenset **is** the requirement "a completed host must never be re-probed unnecessarily".
COMPLETED = frozenset({"validated", "rejected", "admitted", "withdrawn"})

#: The tier an admission assigns unless the caller names the other one. See the module docstring.
ADMISSION_TIER = "shadow"

#: Every tier an admission may assign, most restrictive first. **Tier A is not here and cannot be.**
#:
#: ``shadow``  stored, deduped, attributed, surfaced nowhere. The right lane for a source no one has
#:             evaluated, and the default for exactly that reason.
#: ``B``       searchable and attributable, never enters the story builder. `corpus.shadow_exclusions`
#:             states the whole difference: *"Tier B and shadow differ in exactly one way and it is
#:             this: Tier B is searchable, shadow is not."*
#:
#: B was added because shadow is not a destination the 50,000-outlet target can use. The corpus is
#: ~5,000 Tier A and ~45,000 Tier B (`M14_LANGUAGE_DENSITY_DESIGN.md` §8.1, from a measured row cap),
#: and until this list had a second entry the only durable thing admission could do with a validated
#: host was hide it from readers. Assigning B is still a **partition change** — it takes the host's
#: articles out of the story builder — and `store.admit_source` refuses it without
#: ``accept_partition_change`` exactly as it refuses shadow.
ADMISSION_TIERS = ("shadow", "B")

#: Minutes after which a ``probing`` claim is presumed dead rather than in flight.
#:
#: There is no way to distinguish "the process that claimed this crashed" from "the process that
#: claimed this is still working" without a liveness channel, and inventing one for a job that runs
#: a few times a week would be the expensive answer to a cheap question. So this is a **deliberate
#: bias**: a probe costs at most five requests at >= 2 s of politeness — about ten seconds — so
#: thirty minutes is two orders of magnitude of slack, and erring long means a resumed campaign
#: skips one host rather than that two campaigns hit one publisher at once.
#:
#: `source_campaign.py` reports these rows loudly rather than hiding them, and ``--stale-minutes 0``
#: is the operator's override for "I am certain nothing else is running".
STALE_PROBE_MINUTES = 30.0

#: Hours before an ``incomplete`` host may be probed again. Unlike a rejection this is OUR failure —
#: a timeout, a TLS error, a 5xx — so it is retried, but not immediately: `crawler._fetch_text`
#: already spends its own retry budget inside a single probe, and a host that exhausted that is
#: telling us something about the next few minutes.
INCOMPLETE_RETRY_HOURS = 6.0

#: Days after which a ``rejected`` host may be re-offered. ``None`` means never, and that is the
#: shipped value: a publisher's robots.txt refusal is not a transient condition, and re-asking on a
#: timer is how a discovery pipeline becomes a nuisance. Re-opening one is
#: ``source_campaign.py reopen --hosts ...``, a deliberate act with a name.
REJECTED_RETRY_DAYS = None

#: Days an article may be older than, on a source admitted from the table, before the crawler drops
#: it. `crawler.PublisherCrawlConfig.max_age_days` defaults to 0 (disabled), which is right for a
#: hand-verified config and wrong for one nobody has looked at: a publisher's *archive* sitemap and
#: its *news* sitemap are the same file format, and SCMP's declared sitemap returned **19,962 URLs
#: spanning years**. An article older than the clustering window can never become a story, so it
#: would ingest, occupy a row, and do nothing. Seven days is one window plus a day of slack.
ADMITTED_MAX_AGE_DAYS = 7

#: Verdict -> state. `source_validation.validate` returns exactly these three.
_VERDICT_STATE = {"ADMIT": "validated", "REJECT": "rejected", "INCOMPLETE": "incomplete"}


class Decision(NamedTuple):
    """Whether a host may be probed now, and why not when it may not.

    ``reason`` is never empty when ``allowed`` is False. A campaign that skipped hosts without
    saying why would be indistinguishable from one that finished."""
    allowed: bool
    reason: str = ""


def state_for_verdict(verdict: str) -> str:
    """The state a probe verdict puts a host in.

    Raises on an unknown verdict rather than defaulting. A fourth verdict appearing in
    `source_validation` and silently landing in ``incomplete`` — retryable, forever — is the kind of
    quiet mapping this repository has had to correct four times."""
    try:
        return _VERDICT_STATE[verdict]
    except KeyError:
        raise ValueError(f"unknown validation verdict: {verdict!r} "
                         f"(expected one of {sorted(_VERDICT_STATE)})") from None


def parse_iso(value: "str | None") -> Optional[datetime]:
    """An ISO timestamp as an aware datetime, or ``None``. Naive input is read as UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def retry_after(state: str, now: datetime) -> Optional[str]:
    """When a host in ``state`` becomes probeable again, or ``None`` for never/immediately.

    ``rejected`` returns ``None`` meaning **never** (:data:`REJECTED_RETRY_DAYS` is ``None``);
    ``candidate`` and ``probing`` return ``None`` meaning **no wait**. The two senses are
    distinguished by :func:`may_probe`, which only consults this field for states that have one —
    keeping the column free of a sentinel that would have to be remembered."""
    if state == "incomplete":
        return (now + timedelta(hours=INCOMPLETE_RETRY_HOURS)).isoformat()
    if state == "rejected" and REJECTED_RETRY_DAYS is not None:
        return (now + timedelta(days=REJECTED_RETRY_DAYS)).isoformat()
    return None


def may_probe(row: "dict | None", *, now: datetime, force: bool = False,
              stale_minutes: float = STALE_PROBE_MINUTES) -> Decision:
    """Whether this host may be probed right now.

    This is the whole of "resumable, idempotent, and never re-probed unnecessarily", in one
    function, so the runner cannot implement a second version of the rule by accident.

    * a host we have never heard of is not probed — discovery decides what is a candidate, and a
      probe of an unseeded host would be a request justified by nothing;
    * a **completed** host is not probed (:data:`COMPLETED`), which is what makes a second run of an
      unchanged campaign spend zero requests;
    * an **``incomplete``** host is not probed until its cooloff expires;
    * a **``probing``** host is not probed while its claim is fresh, because another process is
      plausibly mid-request to that publisher right now. This is the only cross-process politeness
      the system has: `crawler.RateLimiter` lives inside one process and cannot see another.

    ``force`` overrides every one of these except the first. A host that is not in the table is
    never probed, forced or not — that bound is what keeps a campaign inside the discovered
    candidate set rather than turning into a crawl of the internet.
    """
    if row is None:
        return Decision(False, "not a candidate — discovery has not offered this host")
    state = row.get("state") or ""
    if state not in STATES:
        return Decision(False, f"unknown admission state {state!r}")
    if force:
        return Decision(True, "forced")
    if state in COMPLETED:
        return Decision(False, f"already {state} — a completed host is not re-probed "
                               f"(reopen it deliberately if the source has changed)")
    if state == "probing":
        claimed = parse_iso(row.get("claimedAt"))
        if claimed is not None and (now - claimed) < timedelta(minutes=max(0.0, stale_minutes)):
            age = (now - claimed).total_seconds() / 60.0
            return Decision(False, f"claimed {age:.1f} min ago and still inside the "
                                   f"{stale_minutes:g}-minute in-flight window — another campaign "
                                   f"may be mid-request to this publisher")
        return Decision(True, "resuming an interrupted probe")
    if state == "incomplete":
        until = parse_iso(row.get("retryAfter"))
        if until is not None and now < until:
            return Decision(False, f"the probe failed on our side; retrying after "
                                   f"{until.isoformat()}")
        return Decision(True, "retrying after an incomplete probe")
    return Decision(True, "never probed")


def check_admission_tier(tier: str) -> None:
    """Raise unless ``tier`` is one of the tiers an admission may assign.

    Not a formality. `corpus.DEFAULT_TIER` is ``"A"``, so the difference between "admitted into a
    non-clustering lane" and "admitted into the clustering corpus" is one string, and the roadmap is
    explicit that Tier A promotion is "gated, manual, and permanently narrow". This function is the
    policy half of that; `store.admit_source` refuses the same value at the write, so neither a new
    caller nor a direct store user can route around it.

    **The set widened from one tier to two, and what it protects did not.** Both ``shadow`` and
    ``B`` are lanes the story builder cannot see, so neither can put an unevaluated source into the
    clustering corpus — which is the whole property this guard exists for. ``"A"`` is refused here
    for the same reason it always was: entering Tier A requires a lean
    (`source_lifecycle.NEEDS_LEAN`) and a clustering counterfactual, and admission has neither."""
    if tier not in ADMISSION_TIERS:
        raise ValueError(
            f"admission may only assign {' or '.join(repr(t) for t in ADMISSION_TIERS)}, not "
            f"{tier!r}. Tier A is M9's decision, made on M8's evidence with a clustering "
            f"counterfactual and a lean — see source_lifecycle.crosses_tier_a. Admission puts a "
            f"source in a lane the story builder cannot see.")


def crawl_config_fields(row: dict) -> dict:
    """An admitted row as keyword arguments for `crawler.PublisherCrawlConfig`.

    Kept here rather than in `crawler` so the mapping is policy and testable without importing the
    crawler, and returned as a plain dict so this module still imports nothing from the repository.

    Four choices worth naming:

    ``publisher``   falls back to the **host** when the registry does not know the outlet — which it
                    usually will not, since discovering unregistered outlets is the entire point.
                    `ingest.Scorer._resolve_outlet` already falls back to the URL's domain, so a
                    host-named publisher is the shape the catalogue would have stored anyway, and it
                    is the shape `corpus._matches` handles best: it tests the host set against the
                    publisher STRING as well as the URL, which is what makes `corpus.sql_exclusions`
                    provably a subset of what `select` drops.
    ``domains``     exactly the one host. This is the security boundary — the set of hosts allowed to
                    yield ARTICLES — and widening it is how a publisher's identity gets attached to a
                    URL it did not publish.
    ``discovery_domains``
                    the feed's host when it differs, and nothing else. `PublisherCrawlConfig`
                    separates these two for the BBC's sake (feeds on ``bbci.co.uk``, journalism on
                    ``bbc.co.uk``); an admitted source gets the same separation for free.
    ``article_pattern``
                    empty unless a human supplied one. `CRAWLER_DESIGN.md`'s sharpest warning is
                    that a pattern matching 0% of discovered URLs makes the crawler ingest nothing
                    while every gate reports healthy — so a **guessed** pattern is worse than none.
                    The probe's sample URLs are stored precisely so one can be written from
                    observation later.
    """
    host = row["host"]
    feed = (row.get("feedUrl") or "").strip()
    kind = "sitemap" if (row.get("discoveredVia") or "") == "news sitemap" else "rss"
    fields = {
        "publisher": (row.get("publisher") or "").strip() or host,
        "domains": (host,),
        "sources": ({"kind": kind, "url": feed},) if feed else (),
        "article_pattern": (row.get("articlePattern") or "").strip(),
        "max_age_days": ADMITTED_MAX_AGE_DAYS,
        "enabled": True,
        # Crawl-policy cadence override (NULL = the global interval). Threaded as data and floored
        # at READ (`crawler.MIN_CRAWL_INTERVAL`), so the record keeps what the operator wrote.
        "interval_seconds": row.get("crawlIntervalSeconds"),
    }
    feed_host = _host_of(feed)
    if feed_host and feed_host != host and not feed_host.endswith("." + host):
        fields["discovery_domains"] = (feed_host,)
    return fields


def _host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url or "").hostname or "").lower()
