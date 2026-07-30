"""push_retry.py — when a failed delivery is tried again, and when it is given up on (Phase B3).

A pure leaf, like ``push_payload``: standard library only, no store, no clock of its own, no network.
Everything here is a decision about *time*, and time is the one thing a scheduler must be able to test
without waiting for it — so ``now`` is always a parameter and the jitter source is injectable.

``docs/BROWSER_PUSH_ARCHITECTURE.md`` §7 already fixed the classification this builds on: timeouts,
connection failures, `429` and `5xx` are **retryable**; `400`/`403`/`413` are **terminal**; `404`/`410`
are terminal *and* prune the subscription. B2 implemented the classification and recorded it. B3 adds
the only thing that was missing — a second attempt.

**Three bounds, and each answers a different question.**

* :data:`MAX_ATTEMPTS` — *how many times.* Without it a permanently-unreachable service turns one
  notification into an unbounded stream of requests.
* :data:`MAX_BACKOFF_SECONDS` — *how far apart.* Without it the exponent runs away and the last
  attempt lands days later, against a payload the push service dropped hours ago.
* :data:`MAX_DELIVERY_AGE_SECONDS` — *for how long overall.* This is the one that matters most, and it
  is not a performance bound: a notification that arrives late enough is not a late notification, it is
  a wrong one. "Breaking news" delivered four hours after the fact describes something that has stopped
  being true, and the reader cannot tell that from the lock screen. It is deliberately equal to the
  transport's own ``ttl`` — the push service would drop the message at the same moment anyway, so
  attempting past it spends requests on a delivery that could no longer succeed.

**Why the delay grows.** A retryable failure means the push service is unwell or unreachable. Retrying
immediately, and at the same rate, adds load to a service that is already failing — which is how a
client turns someone else's degradation into their outage. Growth backs off; jitter keeps a fan-out
that failed together from retrying together.

**Why ``Retry-After`` is a floor and never a ceiling.** When a push service says how long to wait it
knows something we do not, and asking again sooner is the definition of hammering. But an enormous
value must not park a delivery past its usefulness — so a request to wait beyond the age bound is
honoured by *giving up*, not by waiting.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import push_sender

#: Total sends per delivery, first attempt included. Five spans roughly half an hour with the defaults
#: below — long enough to ride out a push service's bad minute, short enough to stay inside the TTL.
MAX_ATTEMPTS = 5

#: Delay after the first failure. The exponent doubles it per subsequent attempt.
BASE_SECONDS = 30.0

#: Ceiling on one gap. Beyond this the growth stops being useful: the age bound will end the ladder
#: before a longer gap could pay for itself.
MAX_BACKOFF_SECONDS = 900.0

#: How old a delivery may get before it is abandoned regardless of attempts remaining. Equal to the
#: transport's ``ttl`` (``push_sender._pywebpush_transport``) — see the module docstring.
MAX_DELIVERY_AGE_SECONDS = 14400.0

#: How long a claimed-but-unresolved row may sit before another run may take it over. Longer than any
#: single send can take (the per-send deadline is seconds), so a row this old means the process died
#: mid-send rather than that a send is still running.
LEASE_SECONDS = 900.0

#: The outcomes worth another attempt. Anything else is either done or a defect on our side.
RETRYABLE = frozenset({push_sender.TIMEOUT, push_sender.TRANSIENT})


def is_retryable(status: str) -> bool:
    """Whether this outcome earns another attempt.

    ``expired`` is excluded even though it is a failure: the subscription is gone, so a retry is
    guaranteed to fail against an address that no longer exists. ``permanent`` is excluded because
    retrying an unchanged request that was rejected for being wrong cannot produce a different
    answer — it can only produce the same one, more often."""
    return status in RETRYABLE


def parse_retry_after(value: "str | int | float | None", *, now: "datetime | None" = None) -> "float | None":
    """RFC 9110 ``Retry-After`` → seconds from now, or ``None`` if it says nothing usable.

    Two forms are legal and push services use both: a delta in seconds (``120``) and an HTTP-date
    (``Wed, 21 Oct 2026 07:28:00 GMT``). A date already in the past yields ``0.0`` — the service is
    telling us the window has opened, not that we should travel backwards — and anything unparseable
    yields ``None`` so the caller falls back to its own backoff rather than to no wait at all."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(int(text)))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return max(0.0, (when - reference).total_seconds())


def backoff_seconds(attempts: int, *, retry_after: "float | None" = None, rng=None) -> float:
    """How long to wait before attempt number ``attempts + 1``.

    ``attempts`` is how many sends have already happened, so the first failure passes ``1``.

    **Equal jitter**, not full jitter: half the computed delay is fixed and half is random. Full
    jitter can schedule a retry almost immediately, which defeats the backing-off; no jitter schedules
    every device in a failed fan-out at the same instant, which reproduces the thundering herd the
    backoff exists to avoid. Halving keeps the growth shape and still spreads the load.

    ``retry_after`` is a **floor**, applied after jitter: a push service asking for more time gets it,
    and one asking for less than our own backoff does not get to shorten it."""
    exponent = max(0, int(attempts) - 1)
    raw = min(BASE_SECONDS * (2 ** exponent), MAX_BACKOFF_SECONDS)
    half = raw / 2.0
    jittered = half + (rng or random.random)() * half
    return max(jittered, float(retry_after)) if retry_after is not None else jittered


def next_attempt_at(*, now: datetime, attempts: int, first_attempted_at: "datetime | None" = None,
                    retry_after: "float | None" = None, rng=None) -> "datetime | None":
    """When to try again — or ``None`` for "stop trying", which is the answer this function exists for.

    Four ways to reach ``None``, and they are not interchangeable in the logs:

    1. the attempt budget is spent (``attempts >= MAX_ATTEMPTS``);
    2. the delivery is already older than :data:`MAX_DELIVERY_AGE_SECONDS`;
    3. the *next* attempt would land past that age — waiting for a deadline we know we will miss is
       strictly worse than admitting it now, because the row sits in a retryable state meanwhile and
       every operator reading the ledger has to work out that it is already doomed;
    4. a ``Retry-After`` long enough to have the same effect.

    :func:`give_up_reason` names which one, so the log line says something an operator can act on."""
    if int(attempts) >= MAX_ATTEMPTS:
        return None
    deadline = _age_deadline(first_attempted_at, now)
    if deadline is not None and now >= deadline:
        return None
    when = now + timedelta(seconds=backoff_seconds(attempts, retry_after=retry_after, rng=rng))
    if deadline is not None and when > deadline:
        return None
    return when


def expired(*, now: datetime, first_attempted_at: "datetime | None" = None) -> bool:
    """Whether this delivery is already past the age at which sending it stops being right.

    Separate from :func:`next_attempt_at` because the two are asked at different moments: that one is
    asked *after* a failure, when there is a result to schedule around, and this one is asked *before*
    an attempt, when the only question is whether to bother. A restart is what makes the difference
    matter — a row scheduled just inside the bound and then left for hours by a deploy would otherwise
    come due and be sent, hours late, describing something that has stopped being true.

    An unknown start time is not an expired one: without ``first_attempted_at`` this answers ``False``,
    because abandoning a delivery for a fact not in evidence is the worse error."""
    deadline = _age_deadline(first_attempted_at, now)
    return deadline is not None and now >= deadline


def give_up_reason(*, now: datetime, attempts: int,
                   first_attempted_at: "datetime | None" = None) -> str:
    """Why :func:`next_attempt_at` returned ``None``. ``"attempts"`` or ``"age"`` — the two have
    different fixes (raise the budget vs. the delivery is simply too late to be worth sending), and a
    log line that says only "gave up" leaves an operator unable to tell them apart."""
    if int(attempts) >= MAX_ATTEMPTS:
        return "attempts"
    return "age"


def _age_deadline(first_attempted_at: "datetime | None", now: datetime) -> "datetime | None":
    """The moment this delivery stops being worth sending. ``None`` when we do not know when it
    started — an unknown age is not an expired one, and inventing a start time would abandon
    deliveries for a fact not in evidence."""
    if first_attempted_at is None:
        return None
    started = first_attempted_at
    if started.tzinfo is None:
        # Stored naive by SQLite. It was written as UTC, so read it back as UTC rather than as local
        # time, which would shift the deadline by the host's offset.
        started = started.replace(tzinfo=timezone.utc)
    return started + timedelta(seconds=MAX_DELIVERY_AGE_SECONDS)
