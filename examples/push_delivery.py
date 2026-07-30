"""push_delivery.py — the Web Push delivery worker (Phase B2).

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
* **pruning on 404/410** — immediate, because a dead endpoint left in place is paid for on every
  future fan-out.

**The poller must never block on network I/O**, so :func:`request_delivery` starts a daemon thread and
returns. One run at a time: if a run is in flight the request is dropped rather than queued, which is
what keeps a slow push service from turning into a growing backlog of overlapping fan-outs.

**What this phase deliberately does not do.** No retries, no backoff, no queue, no batching, no rate
limiting. A ``transient`` failure therefore means the notification is not delivered to that device *at
all* — the claim row records the attempt and its outcome, and nothing picks it up again. That is a
real consequence, chosen over a half-built retry ladder, and the ledger is what a later phase will
select on to add one.
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
import push_sender
import settings_service

CHANNEL = "web_push"

#: Concurrent sends. Small on purpose: the engine is one modest container that is also serving
#: requests and ingesting, and a fan-out is not the most important thing it does.
MAX_WORKERS = 4

#: Wall-clock bound on one fan-out. A run that hits this stops cleanly and the next cycle picks up
#: what is left — unbounded work on a background thread is how a "background" job becomes an outage.
MAX_RUN_SECONDS = 120.0

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
    skipped: int = 0          # already claimed by an earlier cycle
    events: int = 0


# --------------------------------------------------------------------------------------------- #
# The fan-out.
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


def _deliver_one(sender, store_, subscription: dict, notification: dict, lang: str, log) -> str:
    """Claim, send, resolve, and prune if the device is gone. Returns the resulting status, or ``""``
    when the claim was already taken.

    Runs on a pool thread, so it never raises: an exception here would be swallowed by the executor
    and the ledger row would stay ``pending`` with nobody the wiser."""
    nid, sid = notification["id"], subscription["id"]
    try:
        claim = store_.claim_delivery(nid, sid, user_id=subscription["userId"], channel=CHANNEL)
        if claim is None:
            return ""                        # an earlier cycle already delivered (or tried)

        payload = push_payload.build(notification, lang=lang,
                                     sent_at=datetime.now(timezone.utc).isoformat())
        body = push_payload.encode(payload)
        size = len(body.encode("utf-8"))
        if size > push_payload.SOFT_LIMIT_BYTES:
            # Not truncated further: a payload this big is a defect in whatever produced it, and
            # silently mangling it would hide that. Logged, and still attempted.
            log(logging.WARNING, "push_payload_oversize", notificationId=nid, bytes=size)

        log(logging.INFO, "push_send_started", notificationId=nid, subscriptionId=sid,
            kind=notification.get("kind"), bytes=size)
        result = sender.send(subscription, body)
        store_.record_delivery_result(claim, result.status, status_code=result.status_code,
                                      detail=result.detail)

        if result.ok:
            log(logging.INFO, "push_send_succeeded", notificationId=nid, subscriptionId=sid)
        elif result.status == push_sender.TIMEOUT:
            log(logging.WARNING, "push_send_timeout", notificationId=nid, subscriptionId=sid)
        else:
            log(logging.WARNING, "push_send_failed", notificationId=nid, subscriptionId=sid,
                status=result.status, statusCode=result.status_code, detail=result.detail)

        if result.subscription_gone:
            # Immediate, not deferred: a dead endpoint left in place is attempted again on every
            # future fan-out, and its failures crowd out the ones that mean something.
            if store_.delete_push_subscription_by_id(sid) is not None:
                log(logging.INFO, "push_subscription_pruned", subscriptionId=sid,
                    userId=subscription["userId"], statusCode=result.status_code)
        return result.status
    except Exception as exc:                 # noqa: BLE001 — see the docstring
        log(logging.WARNING, "push_send_error", notificationId=nid, subscriptionId=sid,
            error=f"{type(exc).__name__}: {exc}")
        return push_sender.PERMANENT


def run_once(store_, *, now=None, sender=None, log=None) -> RunStats:
    """One fan-out pass. Returns what it did.

    Synchronous and blocking — this is the body the background thread runs, and the seam the tests
    drive directly. Never raises: it is called from a daemon thread whose death would be silent."""
    stats = RunStats()
    log = log or _default_log
    now = now or datetime.now(timezone.utc)
    sender = sender or _sender()
    if sender is None:
        return stats

    deadline = datetime.now(timezone.utc) + timedelta(seconds=MAX_RUN_SECONDS)
    categories = _event_categories(store_, now)
    stats.events = len(categories)

    # Candidates first, so the per-reader work below happens only for readers who could receive
    # anything at all. The mirror is an accelerator: `evaluate(ctx, "push")` still decides consent.
    by_user: dict = {}
    for category in categories:
        for sub in store_.push_subscriptions_for_category(category):
            by_user.setdefault(sub["userId"], {})[sub["id"]] = sub

    # PLAN, then SEND. The planning reads the store on this thread and the sending writes to it on
    # pool threads; interleaving the two means one connection being used concurrently, which SQLite
    # does not forgive. Two phases also make the deadline meaningful — planning is bounded, and what
    # was planned is then all attempted.
    plan = []
    for uid, subs in by_user.items():
        if datetime.now(timezone.utc) >= deadline:
            log(logging.WARNING, "push_run_deadline", plannedReaders=len(plan))
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
                plan.append((sub, notification, lang))

    stats.considered = len(plan)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_deliver_one, sender, store_, sub, notification, lang, log)
                   for sub, notification, lang in plan]
        for future in futures:
            status = future.result()
            if status == "":
                stats.skipped += 1
            elif status == push_sender.SUCCESS:
                stats.sent += 1
            else:
                stats.failed += 1
                if status == push_sender.EXPIRED:
                    stats.pruned += 1

    if stats.considered:
        log(logging.INFO, "push_run_complete", considered=stats.considered, sent=stats.sent,
            failed=stats.failed, pruned=stats.pruned, skipped=stats.skipped)
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
