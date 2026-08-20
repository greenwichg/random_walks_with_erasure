#!/usr/bin/env python3
"""What the email channel has to do, and what it has refused to do. **Sends nothing.**

    dc exec -T api python examples/email_status.py

The companion to ``email_preflight.py``, which answers "is my configuration right". This answers
the question that comes next and is otherwise guesswork: *the configuration is right and I still
have no mail — is there anything to send?*

They fail differently and are therefore separate. A preflight can be green while the queue is empty
(the ordinary state most of the time), and the queue can be full while the relay refuses every
connection. Reading one as the other is how an operator concludes the feature is broken when it is
merely idle.

Run as a SCRIPT, not ``python -c``: the image's WORKDIR is ``/app`` and the modules live in
``/app/examples``, so Python puts them on ``sys.path`` only when it is a script's own directory.
That difference has bitten a documented one-liner before.
"""

from __future__ import annotations

import sys

import email_delivery
import store as store_mod

CHANNEL = "email"
KIND = "weekly_digest"


def main() -> int:
    st = store_mod.Store()

    # 1. WAITING. The one number that explains "no mail arrived" when everything is configured.
    pending = st.undelivered_notifications(KIND, channel=CHANNEL, limit=200)
    print(f"weekly digests with no email delivery yet: {len(pending)}")
    for row in pending[:20]:
        print(f"    user {row['userId']:<5} {row['email'] or '(no address)':<34} {row['dedupeKey']}")
    if len(pending) > 20:
        print(f"    … and {len(pending) - 20} more")

    # The age window is why "waiting" is not the same as "will be sent". A digest older than it is
    # never mailed, so a queue that looks full can still be a queue with nothing due in it.
    print(f"\n  of which inside the {email_delivery.MAX_AGE.days}-day age window, and so actually "
          f"sendable: ", end="")
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    fresh = st.undelivered_notifications(KIND, channel=CHANNEL, limit=200,
                                         since=now - email_delivery.MAX_AGE)
    print(len(fresh))

    # 2. WHO IS CLEARED. A full queue and an empty allowlist is the commonest "nothing happens".
    allow = email_delivery.allowlist()
    if allow is None:
        print("\nallowlist: * — every consenting reader is cleared")
    elif not allow:
        print("\nallowlist: EMPTY — nobody is cleared to receive; nothing will be sent")
    else:
        cleared = [r for r in fresh if email_delivery.allowed_recipient(r["email"] or "", allow)]
        print(f"\nallowlist: {len(allow)} entr{'y' if len(allow) == 1 else 'ies'} — "
              f"{len(cleared)} of the {len(fresh)} sendable digests are for a cleared address")

    # 3. THE LEDGER. `pending` that keeps growing means runs are dying mid-send rather than one
    #    having died; `scheduled` is how deep the retry ladder is.
    backlog = st.delivery_backlog(channel=CHANNEL)
    print(f"\nledger: {backlog.get('pending', 0)} claimed-but-unresolved, "
          f"{backlog.get('scheduled', 0)} waiting on a retry backoff")

    # 4. SUPPRESSED. Permanent, and invisible in the UI — so it has to be visible here.
    suppressed = st.list_email_suppressions(50)
    print(f"\nsuppressed addresses: {len(suppressed)}")
    for row in suppressed:
        print(f"    {row['address']:<34} {row['reason']:<10} {row['statusCode']} "
              f"{(row['detail'] or '')[:60]}")
    if suppressed:
        print("    (a hard bounce is permanent; nothing clears this list automatically)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
