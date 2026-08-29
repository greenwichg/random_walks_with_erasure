"""corpus.py — the clustering corpus boundary.

**M1 of `docs/SCALE_ROADMAP.md`.** The clustering corpus stops being "whatever ``_fetch`` returned"
and becomes an explicitly *selected* projection, with a name, a policy, and a budget that says so
out loud when it binds.

## The two things this closes

**1. A silent truncation that turns more sources into fewer stories.**

``story_service._fetch`` bounds the candidate set by a 6-day time window *and* by
``RWE_STORIES_MAX_SCAN`` rows, newest-first. Its own docstring records what happened the last time
that row cap bound, at 2,000:

    "every provider added shrank the hours those 2000 rows covered, so integrating more sources
    produced FEWER stories (measured: a 12.5-hour effective window against a 6-day clustering
    threshold, 89 stories from a 12,790-article catalog)"

The cap was raised to 60,000, which at today's ~4,650 articles/day covers 12.9 days and never
binds. At 150k/day it covers **9.6 hours**; at 500k/day, **2.9 hours**. The defect is not that a
bound exists — it is that hitting it emits nothing, and its only symptom is fewer stories, which
reads as a clustering regression rather than a bound being hit.

``search_feed_articles`` has always returned ``(rows, total)`` and ``_fetch`` has always discarded
the total. **The evidence was already in the caller's hands and thrown away**, which is the same
shape as `PERFORMANCE.md`'s retention finding: "a ``deleted: 0`` line and a ``74,500 ms`` line
describe the same event. Only one of them was ever printed."

**2. There was no name for "the articles that are allowed to form stories."**

`CORPUS_ARCHITECTURE.md` defines ① Full/Searchable, ② Recommendation and ③ Reads, and has Stories
reading ① *directly*. So the clustering corpus was whatever the fetch happened to return, and there
was nowhere to stand to say "this outlet is searchable but does not form stories" — which is
precisely what shadow ingest, promotion and retirement all need. This module is that place: a new
projection ②′, the same shape of boundary as ②, with the same kind of guardrail test.

## Tiers

``A``       forms and votes in stories. Bounded — see :func:`tier_a_budget`.
``B``       searchable and attributable; **never enters the story builder**.
``shadow``  stored and attributed, surfaced nowhere, pending evaluation.

**Tier is a property of the OUTLET, not of the article** — "does this publisher form stories" is a
fact about a publisher. Deriving the article's tier from its resolved outlet means there is no
migration, no backfill and no possibility of two articles from one outlet disagreeing; and a
demotion (A→B when an outlet turns out to be a syndicator) takes effect on the next build over the
outlet's whole history, which is what a demotion should mean.

The source of truth was an env list, matching ``RWE_CATALOG_BLOCKED_OUTLETS``. That was the right
home for M1 and the wrong home for 50,000 outlets, and :func:`tier_of` was named as the seam for
moving it. **M11 is that move, for the shadow half**: `store.SourceAdmission` carries a ``tier``
column, and :func:`admitted_shadow_hosts` unions it into the shadow set.

Unioned, not replaced, and that is a safety property rather than a migration convenience. See
:func:`admitted_shadow_hosts` — with ``DEFAULT_TIER == "A"``, a table read that came back empty for
any reason would put the entire corpus into the clustering tier, and it would look like an
improvement rather than an error.

## Off is byte-identical, structurally

With neither tier list set **and nothing admitted**, :func:`select` performs **no registry
resolution and no per-row work**, and returns the list it was handed. Not "returns an equal list" —
the same object. The budget report still runs, because that half is the defect fix and it must not
be switchable off by accident. Same discipline as ``RWE_FEED_SCHEDULER``: the feature is off, the
instrument is not.

The admission half is off in the same structural way: :func:`wire_admissions` is never called
implicitly, so a process that has not wired a store — every test that does not ask for one, and
every deployment before the first admission — evaluates :func:`admitted_shadow_hosts` to an empty
frozenset with no query and no import.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import obs_metrics
import outlet_registry
from outlet_registry import default_registry

_logger = logging.getLogger("hidden_view.corpus")

#: The tiers, in order of decreasing privilege. ``A`` is the default for everything, which is what
#: makes turning this on a no-op — see the module docstring.
TIERS = ("A", "B", "shadow")

#: What an outlet is when nothing says otherwise. Grandfathering, deliberately: every outlet the
#: catalog already carries is in the clustering corpus today, and M1's job is to install the
#: boundary, not to move anyone across it. Moving an outlet is a measured decision with its own
#: counterfactual, exactly like every clustering knob in this repo.
DEFAULT_TIER = "A"

#: Articles allowed in the 6-day Tier A window before the build stops fitting its poll cycle.
#:
#: Derived in `docs/SCALE_ROADMAP.md` from the live pipeline profile — ``_fetch`` 2,319 ms linear,
#: ``cluster`` 1,942 ms at exponent 2.05, ``_merge_duplicates`` 1,451 ms quadratic in CLUSTER count,
#: at 22,493 articles. Holding the build to ~60 s (25% of a 600 s cycle's 240 sustainable
#: vCPU-seconds on a t3.medium) puts the ceiling near 83,000 articles, about 3x today's corpus.
#:
#: This is a WARNING threshold, not a gate. Nothing is dropped for exceeding it; the operator is
#: told, because crossing it is the signal that Tier A needs trimming (M2) or that incremental
#: clustering has become necessary (M10). A conservative value is the safe error direction for a
#: warning, and the fit it comes from overstates the measured build by ~35% at k=1.
DEFAULT_TIER_A_BUDGET = 83_000

#: Outlets assigned to a tier other than the default, comma-separated. Each entry is resolved
#: through the registry FIRST, so a value moves the outlet's IDENTITY — every alias and every domain
#: the registry knows for it — rather than the one string somebody happened to type. An entry the
#: registry does not know is treated as a domain and matched subdomain-tolerantly.
_TIER_B_ENV = "RWE_CORPUS_TIER_B"
_SHADOW_ENV = "RWE_CORPUS_SHADOW"
_BUDGET_ENV = "RWE_CORPUS_TIER_A_BUDGET"


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
def _setting(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def tier_a_budget() -> int:
    try:
        v = int(_setting(_BUDGET_ENV))
        return v if v > 0 else DEFAULT_TIER_A_BUDGET
    except (TypeError, ValueError):
        return DEFAULT_TIER_A_BUDGET


def enabled() -> bool:
    """Whether any outlet has been assigned away from :data:`DEFAULT_TIER`.

    False is the shipped state and means :func:`select` short-circuits the tier filter entirely.
    The budget report runs either way.

    **M11 added a third source of assignments** — the admission table — so this is no longer purely
    a question about the environment. It has to be, or admitting a source would write a shadow row
    that :func:`tier_of` never consults, and the source would be crawled straight into Tier A.

    **Both admitted tiers are asked, and forgetting the second one would have been silent.** A
    deployment whose only assignments are Tier B admissions — no environment lists, nothing in
    shadow, which is exactly what a Tier-B-led expansion looks like on day one — would otherwise
    answer ``False`` here, :func:`select` would short-circuit the tier filter entirely, and every
    admitted host would serve as Tier A while the admission table said otherwise. That is this
    codebase's own recurring defect: a gate that cannot fire reading as a gate that passed."""
    return bool(_setting(_TIER_B_ENV) or _setting(_SHADOW_ENV)
                or admitted_shadow_hosts() or admitted_tier_b_hosts())


# --------------------------------------------------------------------------- #
# The admission table (M11) — a second source of shadow assignments, UNIONED with the environment.
# --------------------------------------------------------------------------- #
#: Seconds an admitted-host snapshot is reused before the provider is asked again. Admission is a
#: human-paced operation — a handful of hosts a week — and :func:`tier_of` is called per article, so
#: a query per call is not affordable and a minute of staleness costs nothing.
#:
#: The staleness is safe in the direction that matters. A cache that has not yet seen a new admission
#: reports a SMALLER shadow set, which would be the wrong direction — except that nothing crawls a
#: host until its `crawler.CrawlAdapter` exists, and `crawler.load_config` filters its own rows
#: through :func:`is_shadow`. So a host that this cache has not caught up with is a host that is not
#: being crawled either, and there are no articles from it to mis-tier.
_ADMISSION_TTL_SECONDS = 60.0

#: One provider and one snapshot PER TIER, rather than a second copy of this machinery beside the
#: first. Tier B and shadow ask the same question of the same table and differ only in the value of
#: one column, so a parallel `_tier_b_provider` / `_tier_b_cache` pair would be a second definition
#: of a rule that has exactly one — the drift this repository has had to correct four times already.
_admission_providers: dict = {}
_admission_caches: dict = {}


def _wire(tier: str, provider) -> None:
    """Register (or with ``None`` unregister) one tier's host provider and drop its snapshot."""
    if provider is None:
        _admission_providers.pop(tier, None)
    else:
        _admission_providers[tier] = provider
    _admission_caches.pop(tier, None)


def _admitted(tier: str, *, refresh: bool = False) -> "frozenset[str]":
    """One tier's admitted hosts, snapshot-cached for :data:`_ADMISSION_TTL_SECONDS`."""
    provider = _admission_providers.get(tier)
    if provider is None:
        return frozenset()
    now = time.monotonic()
    at, cached = _admission_caches.get(tier, (0.0, frozenset()))
    if not refresh and at and (now - at) < _ADMISSION_TTL_SECONDS:
        return cached
    try:
        hosts = frozenset(h.strip().lower() for h in (provider() or ()) if h and h.strip())
    except Exception as exc:                    # pragma: no cover - defensive, logged not raised
        _logger.warning(json.dumps({"event": "corpus_admission_read_failed", "tier": tier,
                                    "error": f"{type(exc).__name__}: {exc}",
                                    "detail": "falling back to the environment tier lists alone"}))
        hosts = cached
    _admission_caches[tier] = (now, hosts)
    return hosts


def wire_admissions(provider) -> None:
    """Register a ``() -> frozenset[str]`` of shadow-assigned hosts — normally
    ``store.Store.admitted_shadow_hosts``. ``None`` unregisters.

    **Explicit rather than automatic.** Wiring this from ``Store.__init__`` would mean every store
    the test suite constructs hijacks a module-level global, and a leaked provider pointing at a
    deleted tmp database is the kind of cross-test coupling that takes a day to find. The two places
    that actually serve — the API's startup and `source_campaign.py` — call it in one line."""
    _wire("shadow", provider)


def wire_tier_b_admissions(provider) -> None:
    """Register a ``() -> frozenset[str]`` of Tier-B-assigned hosts — normally
    ``store.Store.admitted_tier_b_hosts``. ``None`` unregisters.

    Separate from :func:`wire_admissions` rather than folded into it, because the alternative was to
    change that function's contract at twenty-odd existing call sites to buy nothing: both wires run
    through :func:`_wire`, so there is still exactly one implementation of the snapshot, the TTL and
    the failure path.

    **A deployment that wires only one of the two is a real state, not a bug.** `_admitted` returns
    an empty set for an unwired tier, which composes with the environment list exactly as it did
    before this existed."""
    _wire("B", provider)


def admitted_shadow_hosts(*, refresh: bool = False) -> "frozenset[str]":
    """Hosts the admission table assigns to the shadow lane, snapshot-cached.

    Empty — and free — when nothing is wired, which is every test that does not ask for this and
    every deployment before M11's first admission. A provider that raises is treated as empty and
    logged once per refresh: **the tier filter must not be able to take the API down**, and the
    failure direction of an empty admission set is the same as today's shipped state rather than a
    novel one.

    ## Why this is unioned with ``RWE_CORPUS_SHADOW`` rather than replacing it

    `DEFAULT_TIER` is ``"A"``. If the table were the source of truth and a read returned nothing —
    a migration not yet applied, a store not wired, a query that raised — **every outlet in the
    corpus would silently become Tier A**. That is the single worst failure this module can have,
    and it would present as "clustering got better" rather than as an error.

    Unioned, the failure mode is instead "the table's assignments are missing", which leaves the
    environment lists exactly as they are today. An operator can also pin an outlet in the
    environment and no table write can un-pin it. `_tier_with` already tests shadow before B "so an
    outlet named in both lands in the more restrictive one"; this is the same principle one level up.
    """
    return _admitted("shadow", refresh=refresh)


def admitted_tier_b_hosts(*, refresh: bool = False) -> "frozenset[str]":
    """Hosts the admission table assigns to Tier B, snapshot-cached.

    The same union, the same failure direction and the same reasoning as
    :func:`admitted_shadow_hosts` — read it there, it is not restated here.

    **What differs is what the lane does.** `shadow_exclusions` puts it plainly: *"Tier B and shadow
    differ in exactly one way and it is this: Tier B is searchable, shadow is not."* So an A → B
    admission takes a host out of the story builder and leaves it in Search, Discover and
    attribution, where an A → shadow admission takes it out of both. That makes this the lane a
    50,000-outlet corpus is mostly made of — and the reason it needed a table at all is
    ``RWE_CORPUS_TIER_B``'s ceiling: M3's S8 puts an environment-variable tier list at ~30,000
    sources, 1 MB of a 2 MB ``ARG_MAX``, below the ~45,000 the target implies."""
    return _admitted("B", refresh=refresh)


@functools.lru_cache(maxsize=8)
def _index(setting: str) -> tuple:
    """One setting string -> ``(canonical names, hosts)``.

    Keyed on the setting string, so an operator or a test changing the env re-parses instead of
    being served a memo of the old value. Bounded at 8 because the key space is operator-controlled
    and small.

    (``ingest._blocked_index`` is the same shape and predates this. Converging them is a real
    tidy-up and a separate change: that one sits in the ingest hot path with its own measurement
    history, and folding it into a new module during M1 would put an unmeasured edit under a
    byte-identical bar that is about something else.)"""
    canonicals, hosts = set(), set()
    for entry in setting.split(","):
        entry = entry.strip()
        if not entry:
            continue
        outlet = default_registry().resolve(entry)
        if outlet is not None:
            canonicals.add(outlet.canonical)
        elif "." in entry:                      # unknown to the registry -> treat it as a domain
            host = outlet_registry._host_of(entry)
            if host:
                hosts.add(host)
    return frozenset(canonicals), frozenset(hosts)


def tier_index() -> dict:
    """What the current settings were understood to mean, per tier.

    Exposed for the same reason ``ingest.blocked_catalog_index`` is: a misspelling, or an
    unregistered outlet named rather than domained, silently matches nothing — and a tier list that
    quietly does nothing is the worst way to find that out.

    **Both** halves are the environment list **unioned** with the admission table's hosts for that
    tier (M11 for shadow; Tier B alongside it) — see :func:`admitted_shadow_hosts` for why that
    composition and not a replacement. Admitted hosts land in the *host* set rather than the
    canonical-name set because that is what they are: a discovered host usually has no registry row
    at all, which is the population discovery works over."""
    b_canonicals, b_hosts = _index(_setting(_TIER_B_ENV))
    canonicals, hosts = _index(_setting(_SHADOW_ENV))
    admitted_b = admitted_tier_b_hosts()
    admitted = admitted_shadow_hosts()
    return {"B": (b_canonicals, b_hosts | admitted_b) if admitted_b else (b_canonicals, b_hosts),
            "shadow": (canonicals, hosts | admitted) if admitted else (canonicals, hosts)}


def _host_match(hosts: frozenset, text: "str | None") -> bool:
    """Subdomain-tolerant host membership: ``example.com`` matches ``news.example.com`` and never
    ``notexample.com``.

    Asks the host set about the article's own label suffixes instead of asking every configured host
    about the article. The predicate is unchanged — see below — but the cost stops depending on how
    many sources are configured, which is the whole of M3's S2.

    ## Why this is the same predicate

    The rule was ``host == h or host.endswith("." + h)`` for some ``h`` in the set. Read the two arms
    as one statement about ``h``:

    * ``host == h`` means ``h`` is the whole host — the ``i = 0`` suffix;
    * ``host.endswith("." + h)`` means ``host`` is ``<something>.<h>``, and the required dot is
      exactly a label boundary — so ``h`` is one of the suffixes at ``i >= 1``.

    Together: the old ``any(...)`` is true exactly when some label suffix of ``host`` is in the set,
    which is what this loop tests. The dot is what makes it safe in both directions: ``example.com``
    matches ``news.example.com`` (suffix ``example.com`` at ``i = 1``) and never
    ``notexample.com``, whose only suffixes are ``notexample.com`` and ``com``.

    ## Why it matters

    ``_matches`` calls this up to four times per article (shadow and Tier B, each against URL and
    publisher), and the retention path calls ``tier_of`` **per article**. Measured against a
    50,000-host set: **3,428.6 µs per ``tier_of`` before, 0.84 µs after** — and flat in the number of
    configured hosts rather than linear in it. At today's ~11,000 publishers the old form was already
    costing ~205 s of held ingest lock on a per-tier retention pass.

    A host has a handful of labels and the set is hashed, so this is O(labels) with a constant that
    does not move. ``tests/test_corpus_host_match.py`` pins both halves: a differential test against
    the original expression, and a guard that this function never *iterates* the host set — which is
    the property that makes it O(labels) rather than a faster-looking rewrite of the same scan.
    """
    host = outlet_registry._host_of(text or "")
    if not host:
        return False
    labels = host.split(".")
    for i in range(len(labels)):
        if ".".join(labels[i:]) in hosts:
            return True
    return False


def _matches(index: tuple, publisher: "str | None", url: "str | None") -> bool:
    """Two-sided identity match, the same rule ``ingest.is_blocked_from_catalog`` uses and for the
    same measured reason: 499 of 671 obituary articles arrive under the parent MASTHEAD's name with
    an ``obits.*`` URL, so resolving only the name lets them through under an identity that is not
    theirs. ``story_service`` has always tested wire membership two-sided
    (``is_wire(publisher) or is_wire_url(url)``); this matches.

    The host set is tested against the PUBLISHER STRING too, not only the URL. An outlet the
    registry does not know is stored under whatever the feed called it, and for broad providers
    that is routinely the bare domain (``ingest.Scorer._resolve_outlet`` falls back to
    ``raw.outlet or _domain_of(raw.url)``) — so a host-configured tier would otherwise miss the rows
    most likely to carry it. It is gated on ``_looks_like_host`` so a display name containing a dot
    still routes to the name path, and it is what makes :func:`sql_exclusions` provably a SUBSET of
    what this function drops."""
    canonicals, hosts = index
    if canonicals:
        reg = default_registry()
        for text in (publisher, url):
            if not text:
                continue
            outlet = reg.resolve(text)
            if outlet is not None and outlet.canonical in canonicals:
                return True
    if hosts:
        if _host_match(hosts, url):
            return True
        if publisher and outlet_registry._looks_like_host(publisher) and _host_match(hosts, publisher):
            return True
    return False


def _tier_with(idx: dict, publisher: "str | None", url: "str | None") -> str:
    """The rule, against an index the caller already holds.

    Split out from :func:`tier_of` so the row loop in :func:`select` reads the environment and
    builds the index ONCE rather than twice per article. That is the same waste the registry memo
    fixed — 60,400 resolve calls over 400 distinct strings, 10% of a whole build — and it is easier
    to not introduce than to find later.

    ``shadow`` is tested before ``B`` so an outlet named in both lands in the more restrictive one:
    a conflicting configuration should fail toward less exposure, not more."""
    if _matches(idx["shadow"], publisher, url):
        return "shadow"
    if _matches(idx["B"], publisher, url):
        return "B"
    return DEFAULT_TIER


def tier_resolver():
    """A ``(publisher, url) -> tier`` callable that reads the settings **once**.

    :func:`tier_of` is the convenient form and re-reads the environment on every call. That is fine
    for a handful of calls and wrong inside a per-article loop, because reading the setting is itself
    linear in the number of configured sources: ``os.environ`` decodes a fresh string on each access,
    so ``_index``'s ``lru_cache`` has to hash the whole value to find its memo. Measured against a
    50,000-host list — a 999,999-byte environment variable — that is **~500 µs per call**, of which
    ~380 µs is the hash and ~59 µs the decode, against **3.6 µs** for the matching itself once the
    index is in hand.

    So the cost that survived M3's D2 is not the *matching*; it is *asking the environment again*.
    :func:`select` and ``audit_source_lifecycle`` already hoist :func:`tier_index` out of their row
    loops for exactly this reason. This function is that hoist, named and public, so the next
    per-article caller does not have to rediscover it — the retention path
    (``corpus_health._tier_age_resolver``) did not, and paid it per article.

    A resolver also gives a pass ONE consistent tier assignment. ``tier_of`` re-reading per call
    means an operator editing ``RWE_CORPUS_SHADOW`` mid-pass could have different articles judged
    against different configurations inside a single retention plan; a resolver cannot.

    The permanent fix is M3's D6 — tier lists do not belong in an environment variable — and this
    measurement is the argument for it: the ``ARG_MAX`` ceiling was the *second* problem with
    storing them there.
    """
    idx = tier_index()
    return lambda publisher, url=None: _tier_with(idx, publisher, url)


def tier_of(publisher: "str | None", url: "str | None" = None) -> str:
    """This article's tier, derived from its outlet's identity.

    Reads the environment on every call — see :func:`tier_resolver` for the per-article form."""
    if not enabled():
        return DEFAULT_TIER
    return _tier_with(tier_index(), publisher, url)


def sql_exclusions() -> "frozenset[str]":
    """Lower-cased ``publisher`` strings a SQL prefilter may safely exclude — **an optimization,
    never the policy**.

    ## Why a prefilter at all

    The row cap is applied in SQL, upstream of :func:`select`. Without this, Tier B rows fill the
    cap and Tier A gets whatever is left — which at 50,000 sources, where Tier B is most of the
    corpus, would truncate the clustering window to a sliver while the tier filter dutifully
    reported that it had removed them. The cap has to bound **Tier A**, not the mixture. That is
    what "bound Tier A" means in M2.

    ## The invariant, and why it holds by construction

    > Every row this set excludes is a row :func:`select` would have dropped anyway.

    One-directional on purpose. The prefilter may miss rows (they fall through to the Python pass,
    which is the contract); it must never remove one the Python pass would keep, or SQL becomes a
    second policy that can silently diverge from the first.

    Both halves are safe:

    * a **canonical** name resolves to itself, so a row stored under it matches ``_matches`` by the
      name side;
    * a **host** is matched by ``_matches`` against the publisher string as well as the URL (see
      that function), so a row whose ``publisher`` IS the host matches there too.

    ## What it cannot express

    Rows whose stored publisher is neither — an alias the registry learned after ingest, or a Tier B
    host appearing only in the URL while the name says something else. Those are the **residue**:
    they still consume cap, ``select`` still drops them, and the report counts them so the fix
    (a registry alias row) is discoverable rather than silent."""
    if not enabled():
        return frozenset()
    out = set()
    for canonicals, hosts in tier_index().values():
        out.update(c.lower() for c in canonicals)
        out.update(h.lower() for h in hosts)
    return frozenset(out)


def shadow_exclusions() -> "frozenset[str]":
    """The SHADOW half of :func:`sql_exclusions` — publisher strings no reader surface may show.

    Tier B and shadow differ in exactly one way and it is this: **Tier B is searchable, shadow is
    not.** A Tier B outlet is a real source whose articles belong in Search, Discover and
    attribution and simply do not form stories. A shadow outlet has not been evaluated yet, so
    nothing about it should reach a reader — it is being watched, not published.

    Kept separate from :func:`sql_exclusions` because the clustering corpus excludes both while the
    reader surfaces exclude only this one. Merging them would make Tier B invisible, which is the
    opposite of what Tier B is for and would delete the whole point of the tier split.

    Reads :func:`tier_index`'s shadow half rather than the environment directly, so an M11-admitted
    source is hidden from readers by the same act that put it in the shadow lane. Reading the
    environment here — as this did before M11 — would have left every admitted source *searchable*
    while the clustering corpus correctly excluded it: stored, not clustered, and surfaced anyway.
    That is precisely the state `shadow` exists to prevent."""
    canonicals, hosts = tier_index()["shadow"]
    if not (canonicals or hosts):
        return frozenset()
    return frozenset({c.lower() for c in canonicals} | {h.lower() for h in hosts})


def tier_b_exclusions() -> "frozenset[str]":
    """The TIER B half of :func:`sql_exclusions` — publisher strings the story builder must not see.

    The complement of :func:`shadow_exclusions`, and deliberately **not** a reader-surface exclusion:
    Tier B is searchable. Nothing in the query path should call this; it exists for the retention
    arm, which needs to act on one tier at a time because the two carry different horizons
    (``RWE_RETENTION_MAX_AGE_DAYS_TIER_B`` against ``..._SHADOW``).

    ## Why this may be used to DELETE and :func:`sql_exclusions` may not

    `sql_exclusions` is documented as *"an optimization, never the policy"*, one-directional: it may
    miss rows because the Python pass is the authority. That contract is safe for a **query** and
    unsafe for a **delete**, where missing a row is harmless but including a wrong one is
    irreversible. This function inherits the safe direction: it is built from `tier_index()["B"]`,
    the same index :func:`tier_of` decides with, so a publisher string it returns is one `tier_of`
    calls Tier B. `_tier_with` tests shadow first, so a host in both lands in shadow — and this set,
    read from the B half alone, can therefore name a host that resolves to shadow. The retention arm
    handles that by acting on the tiers in the same order, never by widening this set."""
    canonicals, hosts = tier_index()["B"]
    if not (canonicals or hosts):
        return frozenset()
    return frozenset({c.lower() for c in canonicals} | {h.lower() for h in hosts})


def is_shadow(publisher: "str | None", url: "str | None" = None) -> bool:
    """The Python contract for shadow membership — what :func:`shadow_exclusions` approximates in
    SQL, and the authority when the two disagree."""
    return tier_of(publisher, url) == "shadow"


# --------------------------------------------------------------------------- #
# The selector
# --------------------------------------------------------------------------- #
def _parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hours(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round((a - b).total_seconds() / 3600.0, 2)


def _default_log(level: int, event: str, **fields) -> None:
    _logger.log(level, json.dumps({"event": event, **fields}, default=str))


def select(rows: list, *, total: "int | None" = None, cap: "int | None" = None,
           window_start: "str | None" = None, log=None,
           report_out: "dict | None" = None) -> list:
    """The clustering corpus: the Tier A rows of ``rows``, with a report of what bound.

    ``rows`` is the SQL slice as returned by ``store.search_feed_articles`` — already time-windowed
    and already truncated at ``cap`` rows, newest-first. ``total`` is that call's pre-pagination
    count, which is what makes the truncation detectable at all.

    Returns the kept rows. Fills ``report_out`` when given, matching the ``veto_stats`` /
    ``band_out`` sink convention ``build_stories`` already uses, so the caller stays a one-liner.

    **The order is honest about what it can and cannot do.** The positional cap is applied in SQL,
    upstream of the tier filter, so once Tier B has members their rows still count against the cap
    before this function ever sees them. Pushing the tier predicate into SQL is M2; ``report_out``
    carries ``capBoundBeforeTier`` so nobody has to infer it.

    Two conditions are reported LOUDLY, at WARNING, because both are silent today:

    * ``clustering_corpus_cap_bound`` — the row cap truncated the requested time window. The report
      names the window actually achieved, in hours, against the one asked for.
    * ``clustering_corpus_over_budget`` — Tier A is past :func:`tier_a_budget`, the size at which
      the build stops fitting its poll cycle. Nothing is dropped; the operator is told.
    """
    emit = log or _default_log
    cap = cap or 0
    kept = rows
    dropped = {"B": 0, "shadow": 0}

    if enabled() and rows:
        idx = tier_index()                      # once for the whole corpus, not once per row
        kept = []
        for r in rows:
            t = _tier_with(idx, r.get("publisher"), r.get("canonicalUrl") or r.get("url"))
            if t == DEFAULT_TIER:
                kept.append(r)
            else:
                dropped[t] = dropped.get(t, 0) + 1
        excluded = len(rows) - len(kept)
        if excluded:
            obs_metrics.incr("clustering_corpus_excluded_total", excluded)

    newest = _parse(rows[0].get("publishedAt")) if rows else None
    oldest = _parse(rows[-1].get("publishedAt")) if rows else None
    requested = _parse(window_start)
    budget = tier_a_budget()
    # `total` is the count BEFORE pagination, so `total > cap` is the truncation, exactly. It is
    # not inferred from `len(rows) == cap`, which would also fire on a window that happens to hold
    # exactly `cap` rows and would report a breach that did not occur.
    cap_bound = bool(cap and total is not None and total > cap)

    residue = dropped["B"] + dropped["shadow"]
    report = {
        # `window` is the count the SQL WHERE matched, so once the tier prefilter is live it is
        # already a TIER A count — which is the point of M2: the cap bounds Tier A, not the mixture.
        "window": total,                       # rows the time window matched, before the cap
        "scanned": len(rows),                  # rows the cap let through
        "kept": len(kept),                     # the clustering corpus
        "droppedTierB": dropped["B"],
        "droppedShadow": dropped["shadow"],
        # Rows the SQL prefilter could NOT express — an alias the registry learned after ingest, or
        # a Tier B host that appears only in the URL. They still consume cap, so this number is the
        # actionable one: a registry alias row moves each of them onto the SQL path.
        "tierResidue": residue,
        "sqlExcludedTerms": len(sql_exclusions()),
        "tiering": enabled(),
        "cap": cap or None,
        "budget": budget,
        "capBound": cap_bound,
        "overBudget": len(kept) > budget,
        # Which of the two bounds is the operative one. Today the "memory backstop" (60,000) sits
        # BELOW the CPU budget (83,000), so the backstop is the binding constraint and the budget
        # warning cannot fire — worth printing rather than leaving to be discovered.
        "binding": ("cap" if cap and cap < budget else "budget"),
        # Now precise rather than pessimistic: the cap is only made worse by tiering to the extent
        # the prefilter missed rows. Before M2 this was true whenever tiering was on at all.
        "capBoundBeforeTier": cap_bound and residue > 0,
        "requestedFrom": window_start,
        "effectiveFrom": oldest.isoformat() if oldest else None,
        "requestedWindowHours": _hours(newest, requested),
        "effectiveWindowHours": _hours(newest, oldest),
    }

    if cap_bound:
        obs_metrics.incr("clustering_corpus_cap_bound_total")
        emit(logging.WARNING, "clustering_corpus_cap_bound",
             window=total, cap=cap, dropped=total - cap,
             requestedFrom=window_start, effectiveFrom=report["effectiveFrom"],
             requestedWindowHours=report["requestedWindowHours"],
             effectiveWindowHours=report["effectiveWindowHours"],
             capBoundBeforeTier=report["capBoundBeforeTier"],
             detail=("the row cap truncated the clustering window; story yield now tracks "
                     "ingestion RATE, so adding sources will produce FEWER stories"))
    if report["overBudget"]:
        obs_metrics.incr("clustering_corpus_over_budget_total")
        emit(logging.WARNING, "clustering_corpus_over_budget",
             kept=len(kept), budget=budget,
             detail=("Tier A is past the size at which the story build fits its poll cycle; "
                     "trim Tier A (M2) or make the build incremental (M10)"))

    if report_out is not None:
        report_out.clear()
        report_out.update(report)
    return kept
