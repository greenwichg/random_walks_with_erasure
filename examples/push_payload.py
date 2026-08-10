"""push_payload.py — the Web Push payload, materialised (Phase B2).

A pure leaf: standard library only, no store, no network, no notification imports. It turns one
persisted notification into the JSON object the service worker will render, exactly as
``docs/BROWSER_PUSH_ARCHITECTURE.md`` §5 freezes it.

**What is deliberately NOT in the payload.** No prose, no translated strings, and no ``titleKey`` /
``bodyKey``. Those are metadata: the worker already holds them for every kind it understands, and
sending them would create a second source of truth for the same mapping (§3). For a kind the worker
does *not* understand they would be useless anyway — it has no catalog entry to look them up in — and
that case is covered by ``href``, which every payload carries so an ignorant worker can still render
something tappable (§6).

**The precedence rule this file implements.** §2: *the payload is a fallback for what the device
cannot derive, never an override of what it can.* Applied twice here — ``lang`` is the reader's
language as known at SEND time (the device's stored value outranks it, because a push can sit under
its TTL for hours), and ``href`` is the server's best guess at a destination (a worker that knows the
kind derives its own).
"""

from __future__ import annotations

import json

#: Payload schema version (§6). Incremented ONLY for a change an old worker cannot safely ignore;
#: adding a field does not increment it, because the contract already requires unknown fields to be
#: ignored rather than fatal.
PAYLOAD_VERSION = 1

#: Bound on free text carried into a payload. The Web Push spec guarantees push services accept at
#: least 4 KB of *encrypted* payload, and exceeding it is a hard rejection of the whole send rather
#: than a truncation — so the one field whose length we do not control (a story headline) is bounded
#: before it can cost the delivery.
MAX_TITLE_CHARS = 200

#: The budget §5 sets for the plaintext. Not enforced by truncation — a payload over it is a defect in
#: whatever produced it, not something to silently mangle — but reported, so the worker can log it.
SOFT_LIMIT_BYTES = 1024

#: Where a kind sends a reader when the worker does not know the kind. Server-computed, so a payload
#: is always tappable; a worker that knows better ignores it.
#:
#: MIRRORS the `href` column of `web/lib/notification-kinds.ts`, and `tests/test_push_delivery.py`
#: reads that file to check it. Only the deep link was pinned before, so these static hrefs could
#: drift silently — and they nearly did when the two report kinds moved off "/report" onto their own
#: period pages, which would have left a push landing on the generic report the inbox no longer uses.
_STATIC_HREFS = {
    "weekly_report": "/report/weekly",
    "monthly_deep_dive": "/report/monthly",
    "recommendations_waiting": "/recommendations",
    "weekly_digest": "/",
    "streak_reminder": "/",
    "blind_spot_alert": "/report",
    "breaking_story": "/stories",
}

#: Kinds whose destination is per-notification: the payload field that names it, and the path it
#: builds. Mirrors `web/lib/notification-kinds.ts` — the two are checked against each other by
#: `tests/test_push_delivery.py`, because a drift here sends readers to the wrong page.
_DEEP_LINKS = {"breaking_story": ("storyId", "/stories/{}")}


def _quote(value: str) -> str:
    """Percent-encode a payload value used as a path segment. A story id is data, never a path."""
    from urllib.parse import quote
    return quote(str(value), safe="")


def href_for(kind: str, payload: "dict | None") -> str:
    """The destination for one notification. Falls back to the kind's static page, then the app root.

    Defensive because the payload is stored JSON that may predate any given shape: an id that is
    missing, blank or not a string yields the static page rather than ``/stories/None``."""
    field_path = _DEEP_LINKS.get(kind)
    if field_path and isinstance(payload, dict):
        field, template = field_path
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return template.format(_quote(value.strip()))
    return _STATIC_HREFS.get(kind, "/")


def _truncated(payload: "dict | None") -> dict:
    """The kind's payload, verbatim except that free text is bounded (see :data:`MAX_TITLE_CHARS`)."""
    out = dict(payload) if isinstance(payload, dict) else {}
    title = out.get("title")
    if isinstance(title, str) and len(title) > MAX_TITLE_CHARS:
        out["title"] = title[:MAX_TITLE_CHARS - 1].rstrip() + "…"
    return out


def build(notification: dict, *, lang: str, sent_at: str) -> dict:
    """One persisted notification → the §5 payload. Pure.

    ``notification`` is a row as ``store.list_notifications`` returns it: the stored body (``kind``,
    ``dedupe_key``, ``payload``, ``created_at``) plus ``id``."""
    kind = str(notification.get("kind") or "")
    payload = _truncated(notification.get("payload"))
    return {
        "v": PAYLOAD_VERSION,
        "notificationId": notification.get("id"),
        "kind": kind,
        "payload": payload,
        "dedupeKey": str(notification.get("dedupe_key") or ""),
        "lang": lang,
        "createdAt": str(notification.get("created_at") or ""),
        "sentAt": sent_at,
        "href": href_for(kind, payload),
    }


def encode(payload: dict) -> str:
    """The wire form. Compact separators because every byte counts against the push service's limit."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def size_bytes(payload: dict) -> int:
    """Plaintext size, for the soft-limit check. The encrypted form is larger; the margin is why
    :data:`SOFT_LIMIT_BYTES` sits well under the 4 KB the spec guarantees."""
    return len(encode(payload).encode("utf-8"))
