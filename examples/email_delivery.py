"""The email channel's delivery worker — one pass over weekly digests that have not been mailed.

Deliberately the same shape as ``push_delivery``: claim before sending, resolve after, retry on a
schedule, never raise. The two channels share the ledger (``notification_deliveries``, whose
``channel`` column was documented from the start as "the axis a future transport (email, mobile
push) extends along"), so the platform's idempotency guarantees are inherited rather than
reimplemented — and a digest already delivered in-app is never re-derived here.

**Scheduling is the notification, not a calendar.** The weekly digest already exists as a
``cadence`` notification deduped on the ISO week (``weekly_digest:2026-W34``), materialised by the
existing evaluator. This worker mails the ones that exist and have no email delivery row yet. That
means the "weekly" schedule has exactly one definition, shared with the in-app card: no second
cron deciding independently when a week starts, and no possibility of the two disagreeing.

**What the email channel adds that push does not need:**

* consent per reader (``email_consent.may_email_digest``), checked at send time rather than at
  materialisation, so a reader who unsubscribes between the two is not mailed;
* a suppression list, because a hard bounce is permanent and re-sending damages deliverability;
* an unsubscribe link, and a refusal to send at all if one cannot be minted;
* a **recipient allowlist**, which is an operator gate rather than a reader preference — see
  :func:`allowlist`.

    RWE_EMAIL_ENABLED=1  the switch (off ⇒ this does nothing at all)
    RWE_EMAIL_SECRET     signs unsubscribe tokens; without it nothing is sent
    RWE_EMAIL_ALLOWLIST  who may receive at all; `*` for everyone. Unset ⇒ NOBODY
    RWE_PUBLIC_URL       the base for report / unsubscribe links
    RWE_EMAIL_MAX_PER_RUN, RWE_EMAIL_RETRY_MAX
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import email_consent
import email_digest
import email_sender
import settings_service

log = logging.getLogger(__name__)

#: No subscription row exists for email — the address is the account's. A fixed sentinel keeps the
#: ledger's UNIQUE(notification_id, channel, subscription_id) meaningful: exactly one email per
#: notification, however many times a run passes over it.
ACCOUNT_DESTINATION = 0

#: Backoff for a retryable outcome (greylisting, a rate limit, a dropped connection). Long, because
#: a digest is not urgent and a relay that just refused us is not helped by being asked again in
#: thirty seconds.
RETRY_BACKOFF = (timedelta(minutes=15), timedelta(hours=1), timedelta(hours=6))

#: How far back a run will look for a digest it has not mailed. **This bound is why opting in does
#: not detonate.**
#:
#: A digest with no email delivery row is not necessarily new work — for every reader with the
#: channel off, it is a row that will never have one. Unbounded, the first reader to tick the box
#: has their whole notification history mailed in a single pass: measured at 30 emails for a
#: 30-week account, arriving at once. The same is true of a reader who unsubscribes and returns.
#:
#: Eight days rather than seven: a run that is a day late — a reboot, a deploy, a host that was
#: down at the wrong minute — must still catch the current week, and the window has to exceed the
#: retry ladder's own reach for the last rung to be worth having. It cannot normally contain two
#: digests, because ISO weeks are exactly seven days apart.
#:
#: A digest older than this is not mailed at all, and should not be: "here is your reading week"
#: about a month nobody remembers is not a digest, it is a surprise.
MAX_AGE = timedelta(days=8)


#: The value of ``RWE_EMAIL_ALLOWLIST`` that means "no restriction". A sentinel rather than an
#: empty string, because the empty string is what an unset or half-edited ``.env`` produces, and
#: that must not be the value that opens general delivery.
ALLOW_ALL = "*"


def allowlist() -> "frozenset[str] | None":
    """Who this deployment may write to. ``None`` means everyone; a set means only these.

    **Fail-closed, and unusually so: unset means NOBODY, not everybody.** Consent (the reader's
    toggle) answers "do I want this"; the allowlist answers "is this deployment cleared to send to
    real people yet", which is an operator's question and has a different safe default. During beta
    the answer is a short list of testers, and the failure to avoid is a config file that loses a
    line and quietly starts mailing a live user base. Going general is then an explicit act —
    ``RWE_EMAIL_ALLOWLIST=*`` — rather than the consequence of an omission.

    Entries are matched case-insensitively and may be either a full address (``me@example.com``) or
    a domain (``@hidden-view.com``), which is what makes the eventual move to a dedicated address
    a one-line change here too."""
    raw = (os.environ.get("RWE_EMAIL_ALLOWLIST") or "").strip()
    if raw == ALLOW_ALL:
        return None
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def allowed_recipient(address: str, allow: "frozenset[str] | None") -> bool:
    """Whether one address is cleared to receive. An address we cannot parse is never cleared."""
    if allow is None:
        return True
    addr = (address or "").strip().lower()
    if "@" not in addr or addr.startswith("@") or addr.endswith("@"):
        return False
    return addr in allow or f"@{addr.rsplit('@', 1)[-1]}" in allow


def _int_env(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


@dataclass
class RunStats:
    """What one pass did. Every skip carries its REASON: "sent 0" is indistinguishable from a
    broken worker until it can say 412 no-address, 38 unsubscribed, 3 suppressed."""

    considered: int = 0
    sent: int = 0
    retried: int = 0
    bounced: int = 0
    skipped: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        return {"considered": self.considered, "sent": self.sent, "retried": self.retried,
                "bounced": self.bounced, "skipped": dict(self.skipped)}


def _base_url() -> str:
    return (os.environ.get("RWE_PUBLIC_URL") or "").strip().rstrip("/")


def _backoff(attempts: int) -> timedelta:
    return RETRY_BACKOFF[min(max(attempts, 1) - 1, len(RETRY_BACKOFF) - 1)]


def _retry_job(store_, delivery: dict, *, now: datetime, retry_max: int) -> "dict | None":
    """Turn a due ledger row back into a sendable job, taking its lease.

    ``lease_delivery`` is compare-and-set on the attempt count, so two workers racing the same
    retry produce one send: the loser sees ``None`` and moves on."""
    attempts = int(delivery.get("attempts") or 1)
    if attempts >= retry_max:
        store_.record_delivery_result(delivery["id"], "failed",
                                      detail=f"giving up after {attempts} attempts")
        return None
    if not store_.lease_delivery(delivery["id"], attempts=attempts, now=now):
        return None                                   # another worker took it
    job = store_.notification_job(delivery["notificationId"])
    if job is None:
        store_.record_delivery_result(delivery["id"], "failed", detail="notification is gone")
        return None
    return {**job, "attempts": attempts + 1, "_delivery_id": delivery["id"]}


def _abandon(store_, stats: "RunStats", delivery_id: "int | None", reason: str) -> None:
    """Decline to send, and CLOSE the ledger row if there is one.

    A retry we decline must be closed rather than merely skipped. ``_retry_job`` has already taken
    its lease, so a bare ``continue`` leaves the row open: it comes back due on the next run, is
    leased again, is declined again, and spends a slot of that run's budget each time until
    ``attempts`` finally reaches the ceiling — three runs to record a decision made on the first.

    Only for decisions **about the reader** (they unsubscribed, the address bounced). Something
    broken at our end must stay open, so that fixing it lets the backlog go out rather than finding
    every delivery already written off. A NEW job has no ledger row yet, so there is nothing to
    close and ``delivery_id`` is ``None``."""
    stats.skipped[reason] += 1
    if delivery_id is not None:
        store_.record_delivery_result(delivery_id, "failed", detail=f"not sending: {reason}"[:255])


def run_once(store_, *, now: "datetime | None" = None, sender=None, kind: str = "weekly_digest",
             limit: "int | None" = None) -> RunStats:
    """One pass. Synchronous, and the seam the tests drive directly.

    **Never raises.** It is called from a background thread whose death would be silent, and one
    reader with a malformed address must not stop the run for everyone behind them."""
    stats = RunStats()
    now = now or datetime.now(timezone.utc)
    sender = sender or email_sender.sender_from_env()
    if sender is None:
        stats.skipped["not-configured"] += 1
        return stats
    if not email_consent.secret():
        # Refusing here rather than sending an email with a dead unsubscribe link. Mail a reader
        # cannot escape is worse than mail they never got.
        log.error("email_no_secret: RWE_EMAIL_SECRET unset — refusing to send unescapable mail")
        stats.skipped["no-unsubscribe-secret"] += 1
        return stats

    allow = allowlist()
    if allow is not None and not allow:
        # Nothing is cleared to receive, so there is no work this pass could do. Returning here
        # rather than scanning and skipping every reader keeps the log honest: one line saying the
        # gate is shut, instead of "sent 0" under a pile of per-reader skips that look like a bug.
        log.warning("email_allowlist_empty: RWE_EMAIL_ALLOWLIST is unset — no recipient is cleared. "
                    "Set it to a comma-separated list of testers, or to %r for general delivery.",
                    ALLOW_ALL)
        stats.skipped["allowlist-empty"] += 1
        return stats

    base = _base_url()
    cap = limit if limit is not None else _int_env("RWE_EMAIL_MAX_PER_RUN", 500)
    retry_max = _int_env("RWE_EMAIL_RETRY_MAX", len(RETRY_BACKOFF))

    # RETRIES FIRST, for the same reason push_delivery does it: they are older, they are closer to
    # their age bound, and a run that runs out of budget should have spent it on the mail that has
    # been waiting longest. `due_deliveries` also recovers rows abandoned `pending` by a process
    # that died mid-send — without that, every send in flight at deploy time is lost silently.
    jobs = [("retry", d) for d in store_.due_deliveries(now=now, limit=cap, channel="email")]
    jobs += [("new", j) for j in
             store_.undelivered_notifications(kind, channel="email", since=now - MAX_AGE,
                                              limit=max(0, cap - len(jobs)))]

    for job_kind, job in jobs:
        if job_kind == "retry":
            resolved = _retry_job(store_, job, now=now, retry_max=retry_max)
            if resolved is None:
                stats.skipped["retry-lost-lease"] += 1
                continue
            job = resolved
        stats.considered += 1
        uid, address = job["userId"], (job.get("email") or "").strip()

        # BEFORE the settings read, and before anything is claimed: an address this deployment is
        # not cleared to write to costs no query and produces no ledger row. NOT `_abandon` — being
        # outside the allowlist is our configuration, not a decision about the reader, so widening
        # the list must let the backlog go out rather than find it already written off.
        if not allowed_recipient(address, allow):
            stats.skipped["not-in-allowlist"] += 1
            continue

        try:
            prefs = settings_service.get(store_, uid)
        except Exception as exc:                       # noqa: BLE001 — one reader must not stop the run
            # NOT abandoned: a settings read that failed is our fault, not a decision about the
            # reader. Left open so the next run can try again.
            log.warning("email_settings_failed uid=%s: %s", uid, exc)
            stats.skipped["settings-error"] += 1
            continue

        reason = email_consent.may_email_digest(prefs, address,
                                                suppressed=store_.email_suppressed(address))
        if reason:
            _abandon(store_, stats, job.get("_delivery_id"), reason)
            continue

        unsubscribe = email_consent.unsubscribe_url(uid, base) if base else ""
        if not unsubscribe:
            # Also our fault (no RWE_PUBLIC_URL), so it stays open — configuring it should let the
            # backlog go out, not find every delivery already written off.
            stats.skipped["no-unsubscribe-url"] += 1
            continue

        # Claim BEFORE sending. The run may be repeated (a crash, an overlapping cycle); the ledger
        # is what stops the same reader being mailed twice for the same week.
        delivery_id = job.get("_delivery_id")
        if delivery_id is None:
            delivery_id = store_.claim_delivery(job["id"], ACCOUNT_DESTINATION, user_id=uid,
                                                channel="email", now=now)
        if delivery_id is None:
            stats.skipped["already-claimed"] += 1
            continue

        content = email_digest.render((job.get("body") or {}).get("payload") or {},
                                      lang=prefs.get("language") or "en",
                                      base_url=base, unsubscribe=unsubscribe,
                                      settings_url=f"{base}/settings" if base else "")
        msg = email_sender.build_message(
            to=address, subject=content["subject"], text=content["text"], html=content["html"],
            sender=sender.sender,
            # RFC 8058 one-click: the mail client shows its own unsubscribe control and the reader
            # never has to find ours. Mail that is easy to leave is mail that gets marked as spam
            # less often, which is what keeps it arriving for everyone else.
            # `Reply-To` is dropped when unset (build_message skips empty values), so a deployment
            # whose From IS a real mailbox is unchanged. It matters for a domain sender like
            # `digest@hidden-view.com`, which is verified for SENDING and has no inbox behind it:
            # without this, Reply goes to an address nobody reads.
            headers={"List-Unsubscribe": f"<{unsubscribe}>",
                     "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                     "Reply-To": email_sender.reply_to(),
                     "Auto-Submitted": "auto-generated"})

        result = sender.send(msg)
        if result.ok:
            store_.record_delivery_result(delivery_id, "sent", status_code=result.code)
            stats.sent += 1
        elif result.bounced:
            store_.record_delivery_result(delivery_id, "failed", status_code=result.code,
                                          detail=result.detail)
            store_.suppress_email(address, reason="bounced", detail=result.detail,
                                  status_code=result.code)
            stats.bounced += 1
        elif result.retryable:
            attempts = int(job.get("attempts") or 1)
            if attempts >= retry_max:
                store_.record_delivery_result(delivery_id, "failed", status_code=result.code,
                                              detail=f"giving up: {result.detail}")
                stats.skipped["retry-exhausted"] += 1
            else:
                store_.record_delivery_result(delivery_id, "pending", status_code=result.code,
                                              detail=result.detail,
                                              next_attempt_at=now + _backoff(attempts))
                stats.retried += 1
        else:
            store_.record_delivery_result(delivery_id, "failed", status_code=result.code,
                                          detail=result.detail)
            stats.skipped[result.status] += 1

    log.info("email_digest_run %s", stats.as_dict())
    return stats


def unsubscribe(store_, token: str) -> "int | None":
    """Honour an unsubscribe link. Returns the user id turned off, or ``None`` for a bad token.

    Turns off BOTH the email channel and the digest toggle it hangs from. A reader clicking
    "unsubscribe" in an email is not asking to keep receiving it in a different place — but the
    in-app notification itself is untouched, because the inbox is the product, not a mailing."""
    uid = email_consent.verify_token(token)
    if uid is None:
        return None
    prefs = settings_service.get(store_, uid)
    notif = dict(prefs.get("notifications") or {})
    cats = {k: dict(v) for k, v in (notif.get("categories") or {}).items()}
    digests = dict(cats.get("digests") or {})
    digests["email"] = False
    cats["digests"] = digests
    settings_service.update(store_, uid, {"notifications": {"weeklyDigest": False,
                                                            "categories": cats}})
    log.info("email_unsubscribed uid=%s", uid)
    return uid
