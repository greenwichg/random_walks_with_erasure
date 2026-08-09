#!/usr/bin/env python3
"""Notifications — end-to-end pipeline audit: "which stage stops it, and why?"

Both channels are wired and both are OFF BY DEFAULT at more than one point, so "nothing arrives" has
many possible causes that look identical from a browser. This walks the pipeline in order and reports
a verdict per stage, so the answer is read rather than guessed.

    docker exec deploy-api-1 python examples/audit_notifications.py
    docker exec deploy-api-1 python examples/audit_notifications.py --email you@example.com

The stages, and what can stop each:

  1 config        three INDEPENDENT switches plus VAPID keys, each defaulting to off/absent
  2 events        breaking-story detection writes `notification_events` (needs switch 1)
  3 in-app        `notifications` rows — materialised ON FETCH, so no flag and no worker
  4 evaluation    per reader: which kinds are due, and for those that are not, WHICH GATE said no
  5 subscriptions `push_subscriptions` rows — needs switch 2 and a browser that granted permission
  6 deliveries    `notification_deliveries` rows — needs switch 3 and the private key

Read-only, and it prints no secret and no endpoint: VAPID keys are reported as present/absent, and
push endpoints (which are bearer-capable URLs) are counted, never shown.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

import notification_delivery as nd              # noqa: E402
import notification_service as ns               # noqa: E402
import settings_service                         # noqa: E402
import store as store_mod                       # noqa: E402

_TRUTHY = {"1", "true", "yes", "on"}


def _on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _present(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def report_config() -> dict:
    """The switches, and what each one being off actually costs.

    They are separate on purpose — registration, detection and sending are three decisions — which
    also means three ways for the pipeline to be silently dark. This prints the consequence next to
    the value so the operator does not have to hold the wiring in their head.
    """
    breaking, reg, delivery = (_on("RWE_BREAKING_NOTIFICATIONS"), _on("RWE_PUSH_ENABLED"),
                               _on("RWE_PUSH_DELIVERY"))
    pub, priv, subj = (_present("RWE_VAPID_PUBLIC_KEY"), _present("RWE_VAPID_PRIVATE_KEY"),
                       _present("RWE_VAPID_SUBJECT"))
    print("\n1 CONFIG (as this process sees it)")
    rows = [
        ("RWE_BREAKING_NOTIFICATIONS", breaking,
         "breaking-story events are detected" if breaking
         else "NO breaking events are ever detected -> no breaking notifications, in-app or push"),
        ("RWE_PUSH_ENABLED", reg,
         "readers may subscribe" if reg
         else "every /api/push/* route 503s and the UI HIDES the toggle -> nobody can subscribe"),
        ("RWE_PUSH_DELIVERY", delivery,
         "the fan-out runs on each poll cycle" if delivery
         else "nothing is ever SENT, however many subscriptions exist"),
    ]
    for name, value, note in rows:
        print(f"   {name:<28} {'on' if value else 'OFF':<4}  {note}")
    for name, value, note in (
        ("RWE_VAPID_PUBLIC_KEY", pub, "browsers subscribe against it"),
        ("RWE_VAPID_PRIVATE_KEY", priv, "signs the sends; delivery is a no-op without it"),
        ("RWE_VAPID_SUBJECT", subj, "the contact the push service is given; required to send"),
    ):
        print(f"   {name:<28} {'set' if value else 'ABSENT':<4}  {note}")
    print("   (key VALUES are never printed — presence only)")
    return {"breaking": breaking, "registration": reg, "delivery": delivery,
            "vapid_public": pub, "vapid_private": priv, "vapid_subject": subj}


def _resolve_reader(st, email: "str | None", user: "int | None") -> "int | None":
    """``--user`` wins; else the account for ``--email``. Names what the store holds when the lookup
    fails, so "no such user" cannot be mistaken for "wrong database"."""
    from sqlalchemy import select
    if user is not None:
        return user
    with st.session() as s:
        row = s.scalar(select(store_mod.User).where(store_mod.User.email == email))
        if row is not None:
            return row.id
        known = [(u.id, u.email) for u in s.scalars(select(store_mod.User)).all()]
    print(f"\nno account for {email!r}; accounts in this store: {known[:10] or 'NONE'}")
    return None


def _count(st, model, **where) -> int:
    from sqlalchemy import func, select
    with st.session() as s:
        q = select(func.count()).select_from(model)
        for col, val in where.items():
            q = q.where(getattr(model, col) == val)
        return int(s.scalar(q) or 0)


def report_tables(st, *, stage: str) -> dict:
    """What the pipeline has actually PRODUCED, table by table. Counts and kinds only.

    Split across two calls so the report reads in PIPELINE ORDER: stages 2-3 are what exists before
    a reader is chosen, stage 4 is that reader's evaluation, and 5-6 are the push tail. Printing the
    push tables before the evaluation that explains them reads as though the tail were the cause.
    """
    from sqlalchemy import func, select

    out: dict = {}
    with st.session() as s:
        out["events"] = int(s.scalar(select(func.count())
                                     .select_from(store_mod.NotificationEvent)) or 0)
        newest_ev = s.scalar(select(func.max(store_mod.NotificationEvent.occurred_at)))
        out["notifications"] = int(s.scalar(select(func.count())
                                            .select_from(store_mod.Notification)) or 0)
        out["notified_readers"] = int(s.scalar(
            select(func.count(func.distinct(store_mod.Notification.user_id)))) or 0)
        by_kind = Counter(dict(s.execute(
            select(store_mod.Notification.kind, func.count())
            .group_by(store_mod.Notification.kind)).all()))
        newest_n = s.scalar(select(func.max(store_mod.Notification.created_at)))
        out["subscriptions"] = int(s.scalar(select(func.count())
                                            .select_from(store_mod.PushSubscription)) or 0)
        out["subscribed_readers"] = int(s.scalar(
            select(func.count(func.distinct(store_mod.PushSubscription.user_id)))) or 0)
        out["deliveries"] = int(s.scalar(select(func.count())
                                         .select_from(store_mod.NotificationDelivery)) or 0)
        by_state = Counter(dict(s.execute(
            select(store_mod.NotificationDelivery.status, func.count())
            .group_by(store_mod.NotificationDelivery.status)).all()))

    if stage == "head":
        print("\n2 EVENT GENERATION   (global occurrences — breaking stories)")
        print(f"   notification_events        {out['events']:>6,}   newest {newest_ev or '-'}")

        print("\n3 IN-APP NOTIFICATIONS   (per reader; materialised ON FETCH — no flag, no worker)")
        print(f"   notifications              {out['notifications']:>6,}   "
              f"across {out['notified_readers']:,} reader(s), newest {newest_n or '-'}")
        for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
            print(f"     {str(kind):<26} {n:>6,}")

    if stage == "tail":
        print("\n5 PUSH SUBSCRIPTIONS   (endpoints are bearer-capable URLs and are never printed)")
        print(f"   push_subscriptions         {out['subscriptions']:>6,}   "
              f"across {out['subscribed_readers']:,} reader(s)")

        print("\n6 DELIVERY LEDGER")
        print(f"   notification_deliveries    {out['deliveries']:>6,}")
        for state, n in sorted(by_state.items(), key=lambda kv: -kv[1]):
            print(f"     {str(state):<26} {n:>6,}")
    out["by_kind"], out["by_state"] = dict(by_kind), dict(by_state)
    return out


def report_reader(st, uid: int) -> dict:
    """The stage no table can answer: for THIS reader, which kinds are due, and for the rest, which
    gate said no and what their setting actually is.

    `_gated` is fail-closed on a missing path, so a preference that was never written and one that
    was written `false` produce the same silence. Printing the resolved path beside the reader's own
    value is the difference between "they turned it off" and "the path does not exist".
    """
    ctx = nd.build_context(st, uid)
    settings = settings_service.get(st, uid)
    due = {n.kind for n in ns.evaluate(ctx)}

    print(f"\n4 EVALUATION for reader {uid}   (what the engine would materialise right now)")
    for k in ns.NOTIFICATION_KINDS:
        for channel in (ns.IN_APP, "push"):
            path = ns.gate_path(k, channel)
            allowed = ns._gated(settings, path) if path else False
            if channel == ns.IN_APP:
                if k.fanout is not None:
                    fired = len(k.fanout(ctx)) if allowed else 0
                    state = f"{fired} due" if allowed else "gated"
                else:
                    triggered = k.kind in due
                    state = "DUE" if triggered else ("condition false" if allowed else "gated")
                verdict = f"{k.kind:<24} {state:<16}"
            else:
                verdict = f"{'':24} {'':16}"
            label = "in-app" if channel == ns.IN_APP else "push  "
            print(f"   {verdict} {label} gate={path or '(none — denied)'} -> "
                  f"{'allow' if allowed else 'DENY'}")
    return {"due": sorted(due)}


def verdict(cfg: dict, tables: dict) -> int:
    """Name the FIRST stage that stops the pipeline, for each channel separately. In-app and push
    fail for different reasons and an operator chasing one should not be handed the other's."""
    print("\nVERDICT")
    if tables["notifications"]:
        # This text described `useNotifications` setting `refetchOnWindowFocus: false`, which froze
        # the bell for the life of a tab. That flag was removed in 145add1, so the sentence became a
        # false statement about the client the moment the fix shipped — a diagnostic that describes a
        # bug it no longer has sends the next operator to a file that is already correct. Rewritten
        # to state what is still true: the query is focus-driven and cached, so a bell that looks
        # empty is now a session/cache question rather than a missing refetch.
        print(f"   in-app  PRODUCING — {tables['notifications']:,} notification(s) exist. If the bell "
              "looks empty,\n"
              "           the loss is in the browser, not the engine. The badge refetches when the "
              "tab regains\n"
              "           focus and caches for 60s (there is no interval polling), so check: the "
              "reader is\n"
              "           signed in (the query is gated on an authenticated session), the tab has "
              "been\n"
              "           refocused since the notification appeared, and the client is not serving a "
              "stale\n"
              "           bundle from before 145add1.")
    else:
        print("   in-app  NOTHING MATERIALISED. There is no flag on this path — it runs on every "
              "fetch of\n"
              "           /api/me/notifications — so the cause is per-reader: run with --email and "
              "read\n"
              "           stage 4, which names the gate.")

    if not cfg["breaking"]:
        print("   events  OFF — RWE_BREAKING_NOTIFICATIONS is not set, so no breaking story will "
              "ever\n"
              "           notify anyone on either channel. The other kinds do not need it.")

    if not cfg["registration"]:
        print("   push    STOPS AT REGISTRATION — RWE_PUSH_ENABLED is off, so the routes 503 and the "
              "UI\n"
              "           hides the toggle. No subscription can exist, so nothing downstream matters.")
    elif not tables["subscriptions"]:
        print("   push    REGISTRATION IS ON but no subscription exists. The browser side did not "
              "complete:\n"
              "           permission denied, no service worker, or a missing/!=87-char VAPID public "
              "key.")
    elif not (cfg["delivery"] and cfg["vapid_private"] and cfg["vapid_subject"]):
        missing = [n for n, ok in (("RWE_PUSH_DELIVERY", cfg["delivery"]),
                                   ("RWE_VAPID_PRIVATE_KEY", cfg["vapid_private"]),
                                   ("RWE_VAPID_SUBJECT", cfg["vapid_subject"])) if not ok]
        print(f"   push    STOPS AT SENDING — subscriptions exist but {', '.join(missing)} "
              "missing.\n"
              "           `push_delivery._sender()` returns None and the fan-out is a no-op.")
    elif not tables["deliveries"] and not cfg["breaking"]:
        # This branch must come BEFORE the poller one below. Push can only ever deliver a kind with
        # a `fanout` (push_delivery._due_for_reader filters to those), and `breaking_story` is the
        # only one — so with breaking detection off there is nothing for the fan-out to carry and an
        # empty ledger is the CORRECT state, not a fault. Without this case the ladder fell through
        # to "check the poller", which sent the operator to a healthy subsystem while stages 1 and 2
        # above already named the cause. Observed doing exactly that on production 2026-08-09.
        print("   push    EXPECTEDLY SILENT — subscriptions and keys are fine, but the only "
              "push-capable\n"
              "           kind is breaking_story and RWE_BREAKING_NOTIFICATIONS is off, so the "
              "fan-out has\n"
              "           nothing to carry. Zero deliveries is CORRECT here. Turn on stage 1 "
              "before\n"
              "           suspecting the poller.")
    elif not tables["deliveries"]:
        print("   push    CONFIGURED AND SILENT — everything is on and no delivery row exists. The "
              "fan-out\n"
              "           runs on the POLLER's post-cycle seam, so check RWE_FEED_POLL and the api "
              "log for\n"
              "           `push_delivery_request_failed`.")
    else:
        print(f"   push    DELIVERING — {tables['deliveries']:,} row(s). Read the state breakdown "
              "above.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("RWE_DB_URL") or os.environ.get("DATABASE_URL"))
    ap.add_argument("--email", default=None, help="also evaluate stage 4 for this reader")
    ap.add_argument("--user", type=int, default=None, help="…or by user id")
    args = ap.parse_args()

    st = store_mod.Store(args.db) if args.db else store_mod.Store()
    try:
        print(f"store          {st.engine.url}")
    except Exception:
        print(f"store          {args.db or '(default)'}")

    cfg = report_config()
    report_tables(st, stage="head")

    uid = _resolve_reader(st, args.email, args.user) if (args.email or args.user) else None
    if uid is not None:
        report_reader(st, uid)
    else:
        print("\n4 EVALUATION   skipped (pass --email or --user to see the per-kind gates)")

    tables = report_tables(st, stage="tail")
    return verdict(cfg, tables)


if __name__ == "__main__":
    raise SystemExit(main())
