"""push_delivery.py — the Web Push delivery worker (Phases B2–B3).

Phase A reaches a reader on their *next request*: the delivery boundary evaluates on fetch, which is
the right latency for an inbox and the wrong one for a push. This module is the inversion — the one
component in the platform that acts without a request having arrived.

``docs/BROWSER_PUSH_ARCHITECTURE.md`` §7 is the contract. What it requires, and where each lives:

* **subscription lookup** — ``store.push_subscriptions_for_category``, the indexed query the
  denormalised mirror exists for. The mirror only narrows the candidate set; consent is still decided
  per reader by ``notification_service.gate_path(kind, "push")`` against real settings.
* **a bounded worker pool** — :data:`MAX_WORKERS` concurrent sends, so a large fan-out cannot exhaust
  connections or memory.
* **per-send timeouts** — :class:`push_sender.WebPushSender` carries the deadline; the run as a whole
  is bounded too, so one wedged service cannot occupy the worker indefinitely.
* **classified retries** — ``push_retry`` decides *when*; the ledger's ``next_attempt_at`` remembers,
  so the ladder survives a restart because it was never in memory to begin with.
* **pruning on 404/410** — immediate, because a dead endpoint left in place is paid for on every
  future fan-out.

**The poller must never block on network I/O**, so :func:`request_delivery` starts a daemon thread and
returns. One run at a time: if a run is in flight the request is dropped rather than queued, which is
what keeps a slow push service from turning into a growing backlog of overlapping fan-outs.

**Three phases, and the boundary between them is not stylistic.** Planning reads the database, sending
touches only the network, recording writes the database — and *only the middle one runs on the pool*.
The store is shared across threads through one SQLAlchemy engine; for an in-memory URL that is a
single connection (``StaticPool``), and interleaving statements from four threads on one connection is
not something SQLite forgives. Confining every read and every write to the worker's own thread makes
the concurrency question disappear rather than answering it carefully once and hoping the next edit
remembers. It also makes a send a pure function of what was planned, which is what lets the retry
ladder be tested without a database at all.

**What this phase deliberately does not do.** No queue, no batching, no rate limiting. The retry ladder
is bounded three independent ways (``push_retry``), and a delivery that exhausts it is a delivery that
did not happen — recorded as such, and not repeated.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import notification_service as ns
import push_payload
import push_retry
import push_sender
import settings_service

CHANNEL = "web_push"

#: Concurrent sends. Small on purpose: the engine is one modest container that is also serving
#: requests and ingesting, and a fan-out is not the most important thing it does.
MAX_WORKERS = 4

#: Wall-clock bound on one fan-out. A run that hits this stops cleanly and the next cycle picks up
#: what is left — unbounded work on a background thread is how a "background" job becomes an outage.
#:
#: It bounds BOTH phases. Planning stops between readers; sending stops between waves of
#: :data:`MAX_WORKERS`, which is the finest grain available without abandoning a send already in
#: flight. One wave costs at most one send timeout, so the real overrun is bounded by that.
MAX_RUN_SECONDS = 120.0

#: Retries planned per run. A backlog drains over several cycles rather than in one burst, which is
#: the point: the service that produced the backlog is the one a burst would land on.
MAX_RETRIES_PER_RUN = 200

#: Total sends one run may attempt. The deadline above is a *backstop*; this is the mechanism.
#:
#: A cap is needed because the fan-out is one job per (notification × device) and every job holds its
#: notification in memory: without it, both the footprint and the run length scale with the subscriber
#: base, and a run outgrows the lease that protects its own rows. At typical latencies a run finishes
#: this many sends in a fraction of the deadline; what the cap actually prevents is the pathological
#: case where every send takes its full timeout.
#:
#: Nothing is lost by capping — an unplanned job is simply not claimed, so the next cycle plans it
#: fresh with no ledger state to unwind. That is the reason to cap in PLANNING rather than to defer
#: after claiming: a claim spends an attempt, and spending one on a send that never happened would
#: shorten the ladder for work the run itself chose not to do.
MAX_JOBS_PER_RUN = 1000

_lock = threading.Lock()
_running = False


def enabled() -> bool:
    """Whether delivery runs at all. Separate from ``RWE_PUSH_ENABLED`` — which governs *registration*
    — so an operator can let readers subscribe and watch the subscription table before anything is
    ever sent to them. Turning delivery on is the act that first puts a notification on a lock screen,
    and it deserves its own switch."""
    raw = os.environ.get("RWE_PUSH_DELIVERY", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _timeout_seconds() -> float:
    raw = os.environ.get("RWE_PUSH_SEND_TIMEOUT_MS", "")
    return (int(raw) / 1000.0) if raw.strip().isdigit() and int(raw) > 0 else 10.0


def _sender() -> "push_sender.WebPushSender | None":
    """The configured sender, or ``None`` when the deployment cannot send. Fail-closed: a missing key
    is not an error to raise on a background thread, it is a reason not to run."""
    private = (os.environ.get("RWE_VAPID_PRIVATE_KEY") or "").strip()
    subject = (os.environ.get("RWE_VAPID_SUBJECT") or "").strip()
    if not (enabled() and private and subject):
        return None
    return push_sender.WebPushSender(private_key=private, subject=subject,
                                     timeout=_timeout_seconds())


@dataclasses.dataclass
class RunStats:
    """What one fan-out did. Returned for the caller's log line and for the tests to assert on."""
    considered: int = 0
    sent: int = 0
    failed: int = 0
    pruned: int = 0
    skipped: int = 0          # already claimed, or taken by a racing worker
    events: int = 0
    retried: int = 0          # attempts that were a second-or-later try
    scheduled: int = 0        # failures that earned another attempt
    exhausted: int = 0        # failures that ran out of attempts, time, or both
    recovered: int = 0        # rows abandoned `pending` by a dead process, taken over
    abandoned: int = 0        # due rows closed without a send (device, consent, or age)


@dataclasses.dataclass(frozen=True)
class _Job:
    """One claimed delivery, ready to send. Everything a pool thread needs and nothing it does not —
    no store, no session, no ids to look anything up with. That is what keeps the send phase free of
    database access, which is the property the module docstring turns on."""
    delivery_id: int
    subscription: dict
    notification: dict
    lang: str
    attempt: int
    first_attempted_at: "datetime | None"


# --------------------------------------------------------------------------------------------- #
# Planning — reads and claims, on the caller's thread.
# --------------------------------------------------------------------------------------------- #
def _event_categories(store_, now) -> "list[str]":
    """Categories with at least one live event. B2 delivers what ``notification_events`` produced —
    the event-driven kinds — rather than everything the registry would evaluate for this reader, so a
    reader's weekly report does not start arriving on their lock screen because push was switched on."""
    try:
        since = (now - timedelta(hours=24)).isoformat()
        events = store_.recent_notification_events(since=since, now=now.isoformat(), limit=200)
    except Exception:                       # noqa: BLE001 — no events readable means nothing to send
        return []
    return sorted({str(e.get("category")) for e in events if e.get("category")})


def _due_for_reader(store_, uid: int, now) -> "list[dict]":
    """The push-channel notifications due for one reader, persisted so they have ids.

    Reuses the whole Phase A decision path — the same context builder, the same pure ``evaluate``, the
    same dedupe ledger — with ``channel="push"``, which is the one thing that differs: consent is read
    from ``notifications.categories.<c>.push`` rather than ``.inApp``.

    Filtered to **fan-out kinds**: those are the ones an event produces, and they are what this phase
    delivers. A cadence or state-alert kind gating the same on every channel would otherwise be pushed
    the moment delivery was enabled, which no reader asked for."""
    import notification_delivery              # lazy: keeps this module out of that import cycle

    ctx = notification_delivery.build_context(store_, uid, now)
    # THE DEDUPE LEDGER IS NOT CONSULTED HERE, and that is load-bearing rather than an oversight.
    # `delivered_keys` answers "does a notification with this key exist for this reader" — level 2 of
    # the platform's four idempotency levels, and it is channel-AGNOSTIC. Left in place, a breaking
    # notification the inbox had already materialised (because the reader opened the app) would look
    # already-delivered to the push channel and never be sent at all.
    #
    # Channel-level idempotency is the DELIVERY ledger's job — level 3, `UNIQUE(notification_id,
    # channel, subscription_id)` — which is per device and per channel, exactly the grain a send needs.
    # Level 2 still holds where it belongs: `record_notifications` below is idempotent, so this cannot
    # mint a second row for the same key.
    #
    # `counts_today` is deliberately KEPT: a per-day cap bounds how much a reader is interrupted, and
    # that is as true of a lock screen as of an inbox.
    ctx = dataclasses.replace(
        ctx, delivery=ns.DeliveryState(delivered_keys=frozenset(),
                                       counts_today=ctx.delivery.counts_today))
    fanout_kinds = {k.kind for k in ns.NOTIFICATION_KINDS if k.fanout is not None}
    due = [n for n in ns.evaluate(ctx, channel="push") if n.kind in fanout_kinds]
    if not due:
        return []

    bodies = [dataclasses.asdict(n) for n in due]
    store_.record_notifications(uid, bodies)   # idempotent; the in-app path may already have written it
    ids = store_.notification_ids_by_dedupe_key(uid, [n.dedupe_key for n in due])
    out = []
    for body in bodies:
        nid = ids.get(body["dedupe_key"])
        if nid is not None:
            out.append({**body, "id": nid})
    return out


def _reader_language(store_, uid: int) -> str:
    """The reader's language as the engine knows it — the payload's FALLBACK only (§4). The device's
    stored value outranks it at render time, because a push can sit under its TTL for hours."""
    try:
        return settings_service.language(store_, uid)
    except Exception:                        # noqa: BLE001 — a language is never worth failing a send
        return "en"


def _consents(store_, uid: int, kind: str) -> bool:
    """Whether this reader still wants this kind on the push channel.

    Checked again on the RETRY path, not only when the notification was first planned. A reader who
    turns push off between two attempts has withdrawn consent, and a ladder that kept going would
    deliver against it — the one place where "we already decided this" is not good enough, because the
    decision is the reader's and they have since changed it. Fail-closed: settings that cannot be read
    do not consent, and neither does a kind the registry no longer has.

    Goes through the registry's own ``gate_path`` and ``_gated`` rather than reading the settings path
    directly, so a channel's preference layout is still known in exactly one place."""
    try:
        k = next((k for k in ns.NOTIFICATION_KINDS if k.kind == kind), None)
        if k is None:
            return False
        return ns._gated(settings_service.get(store_, uid), ns.gate_path(k, "push"))
    except Exception:                        # noqa: BLE001 — unreadable consent is not consent
        return False


def _plan_fresh(store_, *, now, deadline, budget, log, stats) -> "list[_Job]":
    """First attempts: a live event, the readers who consented, and one job per device.

    ``budget`` is how many jobs this run may still take (:data:`MAX_JOBS_PER_RUN` minus whatever the
    retry phase already spent). Reaching it stops planning; nothing is claimed, so the next cycle
    starts from the same place with nothing to undo."""
    if budget <= 0:
        log(logging.WARNING, "push_run_budget_spent", phase="fresh")
        return []
    categories = _event_categories(store_, now)
    stats.events = len(categories)

    # Candidates first, so the per-reader work below happens only for readers who could receive
    # anything at all. The mirror is an accelerator: `evaluate(ctx, "push")` still decides consent.
    by_user: dict = {}
    for category in categories:
        for sub in store_.push_subscriptions_for_category(category):
            by_user.setdefault(sub["userId"], {})[sub["id"]] = sub

    jobs: "list[_Job]" = []
    for uid, subs in by_user.items():
        if datetime.now(timezone.utc) >= deadline:
            log(logging.WARNING, "push_run_deadline", plannedReaders=len(jobs))
            break
        try:
            due = _due_for_reader(store_, uid, now)
        except Exception as exc:             # noqa: BLE001 — one reader must not end the fan-out
            log(logging.WARNING, "push_reader_failed", userId=uid,
                error=f"{type(exc).__name__}: {exc}")
            continue
        if not due:
            continue
        lang = _reader_language(store_, uid)
        for notification in due:
            for sub in subs.values():
                # Checked per JOB, not per reader: one reader with fifty devices is fifty sends, and
                # a budget that only bound the outer loop would not bind at all on the shape of
                # fan-out most likely to be large.
                if len(jobs) >= budget:
                    log(logging.WARNING, "push_run_budget_spent", phase="fresh", planned=len(jobs))
                    return jobs
                claim = store_.claim_delivery(notification["id"], sub["id"], user_id=uid,
                                              channel=CHANNEL, now=now)
                if claim is None:
                    # Already claimed — delivered by an earlier cycle, or open on the retry ladder,
                    # where `_plan_retries` owns it. Either way this run must not send it again.
                    stats.skipped += 1
                    continue
                jobs.append(_Job(delivery_id=claim, subscription=sub, notification=notification,
                                 lang=lang, attempt=1, first_attempted_at=now))
    return jobs


def _plan_retries(store_, *, now, log, stats) -> "list[_Job]":
    """Second-and-later attempts, and the recovery of rows a dead process left claimed.

    Four ways a due row is *abandoned* rather than retried, each closing the delivery for a different
    reason. All four are ordinary; none is an error:

    * the device is gone — unregistered by its reader, or pruned by a 410 on some other notification;
    * the reader withdrew consent for this kind on this channel;
    * the notification itself has been deleted (history pruning outran the ladder);
    * the delivery is older than the age bound, so sending it would describe something that has
      stopped being true.
    """
    try:
        due = store_.due_deliveries(now=now, limit=MAX_RETRIES_PER_RUN,
                                    lease_seconds=push_retry.LEASE_SECONDS, channel=CHANNEL)
    except Exception as exc:                 # noqa: BLE001 — an unreadable ledger is not a crash
        log(logging.WARNING, "push_retry_scan_failed", error=f"{type(exc).__name__}: {exc}")
        return []

    jobs: "list[_Job]" = []
    for row in due:
        recovered = row.get("nextAttemptAt") is None      # was `pending`, not scheduled
        try:
            job = _retry_job(store_, row, now=now, log=log, stats=stats)
        except Exception as exc:             # noqa: BLE001 — one bad row must not end the scan
            log(logging.WARNING, "push_retry_plan_failed", deliveryId=row.get("id"),
                error=f"{type(exc).__name__}: {exc}")
            continue
        if job is None:
            continue
        # The lease is taken LAST, after every reason to abandon has been ruled out: it is the write
        # that costs another attempt, and spending one to discover the device is gone would burn the
        # budget on lookups rather than on sends.
        if not store_.lease_delivery(row["id"], attempts=row["attempts"], now=now):
            stats.skipped += 1               # another worker took it between the scan and here
            continue
        if recovered:
            stats.recovered += 1
            log(logging.WARNING, "push_delivery_recovered", deliveryId=row["id"],
                subscriptionId=row["subscriptionId"], attempts=row["attempts"])
        stats.retried += 1
        jobs.append(job)
    return jobs


def _retry_job(store_, row: dict, *, now, log, stats) -> "_Job | None":
    """One due ledger row → a job, or ``None`` after closing it. See :func:`_plan_retries`."""
    def abandon(reason: str) -> None:
        # The last classification is kept, EXCEPT for a row that never got one. A recovered row is
        # `pending`, and writing that back with a completion time would leave the ledger holding a
        # row that is simultaneously in flight and finished — a state the schema's own docstring says
        # cannot happen. `timeout` is the truthful classification for a send whose answer we never
        # learned, which is exactly what an unresolved claim is.
        last = row.get("status") or ""
        stats.abandoned += 1
        store_.record_delivery_result(row["id"],
                                      push_sender.TIMEOUT if last in ("", "pending") else last,
                                      status_code=row.get("statusCode"),
                                      detail=f"abandoned:{reason}"[:255], next_attempt_at=None)
        log(logging.INFO, "push_retry_abandoned", deliveryId=row["id"],
            subscriptionId=row["subscriptionId"], attempts=row["attempts"], reason=reason)

    # Age first, before anything is loaded: a delivery past its usefulness is not worth a database
    # read, let alone a send. Checked here rather than only after a failure so that a row scheduled
    # just inside the bound, and then left by a restart, still stops at it.
    if push_retry.expired(now=now, first_attempted_at=row.get("firstAttemptedAt")):
        abandon("age")
        return None

    subscription = store_.push_subscription_by_id(row["subscriptionId"])
    if subscription is None:
        abandon("subscription_gone")
        return None
    notification = store_.notification_by_id(row["notificationId"])
    if notification is None:
        abandon("notification_gone")
        return None
    if not _consents(store_, row["userId"], str(notification.get("kind") or "")):
        abandon("consent_withdrawn")
        return None

    return _Job(delivery_id=row["id"], subscription=subscription, notification=notification,
                lang=_reader_language(store_, row["userId"]), attempt=row["attempts"] + 1,
                first_attempted_at=row.get("firstAttemptedAt"))


# --------------------------------------------------------------------------------------------- #
# Sending — network only, on the pool. No store access anywhere below this line.
# --------------------------------------------------------------------------------------------- #
def _send(sender, job: _Job, log) -> "push_sender.SendResult":
    """One send. Never raises: it runs on a pool thread, where an exception would be swallowed by the
    executor and the ledger row would stay ``pending`` until the lease recovered it — a delay for
    something that should have been recorded immediately."""
    nid, sid = job.notification["id"], job.subscription["id"]
    try:
        payload = push_payload.build(job.notification, lang=job.lang,
                                     sent_at=datetime.now(timezone.utc).isoformat())
        body = push_payload.encode(payload)
        size = len(body.encode("utf-8"))
        if size > push_payload.SOFT_LIMIT_BYTES:
            # Not truncated further: a payload this big is a defect in whatever produced it, and
            # silently mangling it would hide that. Logged, and still attempted.
            log(logging.WARNING, "push_payload_oversize", notificationId=nid, bytes=size)

        log(logging.INFO, "push_send_started", notificationId=nid, subscriptionId=sid,
            kind=job.notification.get("kind"), attempt=job.attempt, bytes=size)
        return sender.send(job.subscription, body)
    except Exception as exc:                 # noqa: BLE001 — see the docstring
        log(logging.WARNING, "push_send_error", notificationId=nid, subscriptionId=sid,
            error=f"{type(exc).__name__}: {exc}")
        return push_sender.SendResult(push_sender.PERMANENT, None, f"error:{type(exc).__name__}")


# --------------------------------------------------------------------------------------------- #
# Recording — writes the store, back on the caller's thread.
# --------------------------------------------------------------------------------------------- #
def _record(store_, job: _Job, result, *, now, log, stats) -> None:
    """Resolve one delivery: schedule another attempt or close it, and prune a device the push service
    has declared gone."""
    nid, sid = job.notification["id"], job.subscription["id"]
    schedule = None
    if push_retry.is_retryable(result.status):
        schedule = push_retry.next_attempt_at(
            now=now, attempts=job.attempt,
            first_attempted_at=job.first_attempted_at,
            retry_after=push_retry.parse_retry_after(result.retry_after, now=now))

    store_.record_delivery_result(job.delivery_id, result.status, status_code=result.status_code,
                                  detail=result.detail, next_attempt_at=schedule)

    if result.ok:
        stats.sent += 1
        log(logging.INFO, "push_send_succeeded", notificationId=nid, subscriptionId=sid,
            attempt=job.attempt)
        return

    stats.failed += 1
    if result.status == push_sender.TIMEOUT:
        log(logging.WARNING, "push_send_timeout", notificationId=nid, subscriptionId=sid,
            attempt=job.attempt)
    else:
        log(logging.WARNING, "push_send_failed", notificationId=nid, subscriptionId=sid,
            attempt=job.attempt, status=result.status, statusCode=result.status_code,
            detail=result.detail)

    if schedule is not None:
        stats.scheduled += 1
        log(logging.INFO, "push_retry_scheduled", notificationId=nid, subscriptionId=sid,
            attempt=job.attempt, nextAttemptAt=schedule.isoformat(),
            retryAfter=result.retry_after)
    elif push_retry.is_retryable(result.status):
        # Retryable, but out of budget. Distinguished from a terminal failure because the fix is
        # different: this says the push service never came back, not that our request was wrong.
        stats.exhausted += 1
        log(logging.WARNING, "push_retry_exhausted", notificationId=nid, subscriptionId=sid,
            attempts=job.attempt, status=result.status,
            reason=push_retry.give_up_reason(now=now, attempts=job.attempt,
                                             first_attempted_at=job.first_attempted_at))

    if result.subscription_gone:
        # Immediate, not deferred: a dead endpoint left in place is attempted again on every future
        # fan-out, and its failures crowd out the ones that mean something.
        if store_.delete_push_subscription_by_id(sid) is not None:
            stats.pruned += 1
            log(logging.INFO, "push_subscription_pruned", subscriptionId=sid,
                userId=job.subscription.get("userId"), statusCode=result.status_code)


def _send_all(sender, jobs: "list[_Job]", *, deadline, log, stats) -> "list[tuple]":
    """Every planned send, in waves of :data:`MAX_WORKERS`, stopping at the deadline.

    Waves rather than one continuous pool, because a continuously-fed pool has no point at which the
    deadline can be consulted: submitting is instant, so every job is queued before the first second
    has passed and the "bound" bounds nothing. A wave costs at most one send timeout, which is the
    finest grain available without abandoning a request already in flight.

    A job left unsent keeps its claim and is recovered by the lease — the same machinery that recovers
    a run killed mid-send, and for the same reason: from the ledger's point of view an attempt that
    did not happen and an attempt whose outcome was never written are the same unresolved row. This is
    the emergency path; :data:`MAX_JOBS_PER_RUN` is what normally keeps a run inside its deadline."""
    results: "list[tuple]" = []
    for start in range(0, len(jobs), MAX_WORKERS):
        if datetime.now(timezone.utc) >= deadline:
            log(logging.WARNING, "push_run_deadline", phase="send", sent=len(results),
                unsent=len(jobs) - len(results))
            break
        wave = jobs[start:start + MAX_WORKERS]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results += list(zip(wave, pool.map(lambda job: _send(sender, job, log), wave)))
    return results


def run_once(store_, *, now=None, sender=None, log=None) -> RunStats:
    """One fan-out pass. Returns what it did.

    Synchronous and blocking — this is the body the background thread runs, and the seam the tests
    drive directly. Never raises: it is called from a daemon thread whose death would be silent.

    **Retries are planned before fresh sends.** They are older, they are closer to their age bound, and
    a run that runs out of time should have spent it on the work that expires soonest."""
    stats = RunStats()
    log = log or _default_log
    now = now or datetime.now(timezone.utc)
    sender = sender or _sender()
    if sender is None:
        return stats

    deadline = datetime.now(timezone.utc) + timedelta(seconds=MAX_RUN_SECONDS)
    jobs = _plan_retries(store_, now=now, log=log, stats=stats)
    jobs += _plan_fresh(store_, now=now, deadline=deadline,
                        budget=MAX_JOBS_PER_RUN - len(jobs), log=log, stats=stats)
    stats.considered = len(jobs)

    for job, result in _send_all(sender, jobs, deadline=deadline, log=log, stats=stats):
        try:
            _record(store_, job, result, now=now, log=log, stats=stats)
        except Exception as exc:             # noqa: BLE001 — one unrecorded row must not lose the rest
            log(logging.WARNING, "push_record_failed", deliveryId=job.delivery_id,
                error=f"{type(exc).__name__}: {exc}")

    if stats.considered or stats.abandoned:
        log(logging.INFO, "push_run_complete", considered=stats.considered, sent=stats.sent,
            failed=stats.failed, pruned=stats.pruned, skipped=stats.skipped,
            retried=stats.retried, scheduled=stats.scheduled, exhausted=stats.exhausted,
            recovered=stats.recovered, abandoned=stats.abandoned)
    return stats


def _default_log(level: int, event: str, **fields) -> None:
    logging.getLogger("rwe.push").log(level, {"event": event, **fields})


# --------------------------------------------------------------------------------------------- #
# The seam the poller calls. Nothing below ever blocks the caller.
# --------------------------------------------------------------------------------------------- #
def request_delivery(store_, *, log=None) -> bool:
    """Ask for a fan-out on a background thread. Returns whether one was started.

    The poller's thread ingests articles; a fan-out is network I/O against a third party, and blocking
    ingestion on it would trade a delayed notification for a stale corpus — the worse of the two.

    **One run at a time, and a request during a run is dropped rather than queued.** A slow push
    service would otherwise turn every poll cycle into another overlapping fan-out, and the work is
    idempotent anyway: whatever this run does not reach, the next cycle will."""
    global _running
    if _sender() is None:
        return False
    with _lock:
        if _running:
            return False
        _running = True

    def _run():
        global _running
        try:
            run_once(store_, log=log)
        except Exception as exc:             # noqa: BLE001 — a daemon thread dying silently is worse
            (log or _default_log)(logging.WARNING, "push_run_failed",
                                  error=f"{type(exc).__name__}: {exc}")
        finally:
            with _lock:
                _running = False

    threading.Thread(target=_run, name="push-delivery", daemon=True).start()
    return True
