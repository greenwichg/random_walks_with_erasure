# Notification Platform — Phase A (in-app)

**Status:** shipped, OFF in production (`RWE_BREAKING_NOTIFICATIONS=0`).
**Scope:** the reusable notification platform plus its first event-driven kind, *breaking stories*.
Browser push (Web Push, service worker, VAPID, a delivery worker) is **Phase B** and is not built.

---

## 1. What already existed, and what Phase A added

The product shipped with a notification foundation: a pure decision leaf
(`examples/notification_service.py`), a delivery boundary that reads persisted producer state and
materialises rows (`examples/notification_delivery.py`), a store (`notifications` table + a dedupe
ledger), an API (`GET /api/me/notifications`), and a header panel. Every kind it carried was derived
from **one reader's own state** — their report, their streak, their unopened recommendations.

Breaking news is the first kind that is **not** about the reader. It is a *global occurrence*: one
story breaks, and every reader who opted in should be told about it once. Phase A is the smallest set
of changes that makes the existing platform able to express that.

| Commit | What it added |
|---|---|
| A1 | `notification_events` — the global trigger table, with `UNIQUE(source_type, source_id)` |
| A2 | `notifications.categories.{breaking,digests,recommendations,product}.{inApp,push}` preferences |
| A3a | Framework: `EventInputs`, fan-out (`NotificationKind.fanout`), `mode="discrete"`, `max_per_day` |
| A3b | The `breaking_story` kind itself — category gating, daily cap, payload |
| A4 | The delivery boundary reads events + today's counts into the context |
| A5 | `examples/story_events.py` — the producer, on the poller's post-cycle seam |
| A6 | Web presentation + deep link, i18n across 5 catalogs, compose wiring, this document |
| — | *Retrospective follow-ups (see `docs/NOTIFICATION_PHASE_A_RETROSPECTIVE.md`):* channel-aware gating (`gate_path`), and the reader-facing switch in Settings |

---

## 2. The one idea the design turns on: **level vs edge**

`story_intelligence.compute_freshness` returns a **band**, and a band is a *level* computed over a
rolling window. A story crosses into `"Breaking"`, falls out as the window slides past its burst, and
crosses back in when a second wave of coverage lands. A notification written as `if band ==
"Breaking"` therefore fires again, and again, for one story.

What a reader should be told about is the **edge**: the first moment it became true.

Nothing in the producer remembers that edge. The database does:

```sql
UNIQUE (source_type, source_id)      -- notification_events
```

`store.record_notification_event(...)` returns `True` the first time a `(story_breaking, story-id)`
pair is written and `False` for every later cycle **and every concurrent one**. That return value *is*
the edge. The consequence is that `story_events.detect_breaking_stories` holds no state, needs no
"already announced" set, is safe to run on every poll cycle, and is safe to run from more than one
process — properties that a Python-side `seen` set would not have had.

---

## 3. Four levels of idempotency

Each answers a different question, and none subsumes another.

| Level | Constraint | Stops |
|---|---|---|
| Event | `UNIQUE(source_type, source_id)` | the same occurrence being recorded twice (the level/edge problem) |
| Notification | `UNIQUE(user_id, kind, dedupe_key)` | the same occurrence reaching one reader twice |
| Delivery | `UNIQUE(notification_id, channel, subscription_id)` | *(Phase B)* the same notification being pushed twice |
| Display | Web Notification `tag` | *(Phase B)* two OS-level banners for one story |

Phase A implements the first two. The dedupe key for a breaking notification is `ev:{event_id}` —
keyed on the **event row**, not the story id, because a story id is derived from its earliest
published member and can move when a backfill discovers an earlier article. An event row is immutable
once written.

Two properties of the notification level are worth stating exactly, because both look like bugs and
neither is:

* **The dedupe ledger and the display history are the same table.**
  `delivered_notification_keys` reads dedupe keys out of `notifications`, and `prune_notifications`
  deletes settled rows past 200 per user — so pruning history also erases idempotency. It is not
  reachable today: a breaking event is deliverable for 6 h, and reaching 200 notifications inside 6 h
  is impossible under a 5/day cap. A future higher-volume kind could walk into it, and the fix then is
  a ledger that outlives the row, not a bigger `keep`.
* **The daily cap is very nearly, but not exactly, a hard bound.** Two concurrent fetches can each
  compute a budget of 5 before either commits. They almost always cancel out, because dedupe keys are
  derived from event ids: both requests see the same events, compute the same keys, and the loser's
  inserts collide on `UNIQUE(user_id, kind, dedupe_key)`. The cap can only be exceeded when a *new*
  event lands between the two context builds, and then by one or two rows. Locking the read would cost
  more than the overrun it prevents.

---

## 4. Three notification lifecycles

`NotificationKind.mode` was already a two-valued distinction; Phase A added the third. The delivery
boundary treats each differently, and getting this wrong is the most likely way to break the feature.

| Mode | Examples | Auto-resolve when the condition clears? | Collapse to one outstanding row? |
|---|---|---|---|
| `cadence` | weekly report, monthly deep dive, weekly digest | no | no |
| `event` | recommendations waiting, streak reminder, blind-spot alert | **yes** | **yes** |
| `discrete` | **breaking story** | no | no |

A `cadence` kind is a periodic *artifact*: week 30's report stays a real thing after week 31 arrives.
An `event` kind is a *state alert*, true only while its condition holds — so an unseen one
auto-resolves, and at most one may be outstanding per kind. A `discrete` kind is a one-time
*occurrence*, and it must receive **neither** treatment:

* auto-resolve would erase a breaking alert the moment the story stopped breaking, when the reader
  should still see that it broke; and
* collapsing would keep one row for every story ever, when one row *per story* is the entire point.

This holds **by construction rather than by a check**: `inactive_event_kinds` filters on `mode ==
"event"`, and the collapse branch tests membership of `EVENT_KINDS`. A discrete kind is in neither, so
A4 required no code change at the delivery boundary at all. `DISCRETE_KINDS` is exported so the
exclusion is explicit and testable in both directions; adding it to either branch is the mistake to
avoid.

---

## 5. The flow, end to end

```
poller cycle (engine, background thread)
  └─ story_service.request_warm(...)          rebuild the story cache
  └─ story_events.detect_breaking_stories(...)  ← PRODUCER
        publisherCount >= RWE_BREAKING_MIN_PUBLISHERS   (quality bar)
        compute_freshness(...)["band"] == "Breaking"    (the LEVEL)
        store.record_notification_event(...) -> True    (the EDGE)
             ↓
      notification_events row  { category: "breaking", expires_at: now + TTL }
             ↓
GET /api/me/notifications  (the reader's next request — evaluate-on-fetch)
  └─ notification_delivery.build_context(...)
        _recent_events(...)   → EventInputs      (fail-SOFT: unreadable ⇒ ())
        _counts_today(...)    → DeliveryState    (fail-CLOSED: unreadable ⇒ the ceiling)
  └─ notification_service.evaluate(ctx, channel="in_app")
        gate:   gate_path(kind, channel) -> notifications.categories.breaking.inApp
        fanout: one (dedupe_key, payload) per unexpired event
        budget: BREAKING_MAX_PER_DAY (5) minus what today already delivered
  └─ store.record_notifications(...)           one row per story, deduped
             ↓
header panel → notificationPresentation("breaking_story") + notificationHref(kind, payload)
             → /stories/{storyId}
```

**Why the producer runs on the poller and not in a request handler.** Emitting from `/api/stories`
would make a GET write, and the freshness band is only recomputed off the request path here anyway.
`detect_breaking_stories` never raises: a failure must cost breaking notifications and nothing else,
because its caller is the ingest loop and ingestion matters more than an alert about it.

**Why the fail-open/fail-closed postures differ.** They are chosen per read, by consequence. Events
feed a *supplementary* kind, so an unreadable event table degrades to `()` and the reader still gets
their report, streak and recommendation notifications. Counts feed a *cap*, and a cap that cannot read
its counter must not conclude "nothing sent yet" — so it returns the ceiling and closes. **A cap must
never be able to raise itself.**

---

## 6. Reader controls

Preferences live under `notifications.categories` in normalised settings (`settings_service`), one
channel row per category. `email` is present only where something can actually be mailed, because a
leaf is a promise that a switch does something:

```json
"categories": {
  "breaking":        { "inApp": true, "push": false },
  "digests":         { "inApp": true, "push": false, "email": false },
  "recommendations": { "inApp": true, "push": false },
  "product":         { "inApp": true, "push": false }
}
```

Every channel but in-app defaults **off**, and that is consent rather than caution: a channel nobody
opted into is not permission, and defaulting `email` on would have mailed every existing reader on
deploy day.

A kind names its **category**, not a path: `gate_path(kind, channel)` derives
`notifications.categories.breaking.<inApp|push|email>`, so the same kind can be on for one channel
and off for another — which is what a reader means by "notify me, but not on my phone". Unset paths
and unknown channels both **fail closed**: consent is per channel, and a transport nobody has written
a preference for must not inherit consent given for a different one.

The six kinds that predate channels keep a single flat `setting_path`, and that path gates **in-app
only**. It used to answer the same on every channel, on the reasoning that there was no per-channel
toggle to consult for them; the weekly digest email made that false, and the old behaviour was
consent laundering — `notifications.weeklyDigest` was ticked when the app was the only place a
notification could appear, so it answers *whether*, never *where*. Every other channel now resolves
to `""` and denies, and a transport that wants a legacy kind carries its own opt-in
(`notifications.categories.digests.email`, checked by `email_consent.may_email_digest` **on top of**
the flat toggle). In-app is byte-identical before and after; `push_delivery` was already filtering
`evaluate` down to fan-out kinds to dodge the same hazard at its call site.

Exposing this took explicit work at two boundaries, both of which fail silently when missed:

* **The API.** FastAPI's `response_model` filters output to declared fields, so an undeclared
  preference group is stripped from every response — a preference that is neither readable nor
  settable. `NotificationPrefsModel` and `NotificationPrefsUpdate` both carry `categories`.

  **This is per LEAF, not just per group, and it bit the email channel.** `digests.email` existed
  in `DEFAULT_SETTINGS`, in `settings_service`, in the engine, and in the UI — but
  `NotificationCategoryModel` declared only `inApp` and `push`, so the route answered `200` and
  discarded the field in both directions. The toggle could not be switched on, and nothing
  anywhere said why. Every test of the feature drove `settings_service` directly and saw a working
  system; only a round trip through HTTP shows it. `digests` now has its own
  `NotificationDigestCategoryModel` / `…Update` pair, and
  `test_every_channel_leaf_in_the_schema_survives_the_api` derives the check from
  `DEFAULT_SETTINGS` so the next channel is covered the day it lands rather than the day someone
  remembers this paragraph.
* **The web.** `Settings.notifications.categories` is declared in `types/domain.ts` and the
  Notifications card renders the in-app switch for `breaking`. Only in-app: a control for a channel
  that cannot deliver would be a promise the product doesn't keep. `diffSettings` recurses two levels
  so flipping one channel ships one leaf — the engine deep-merges, so a patch restating unchanged
  siblings would overwrite a change made on another device.

Two limits bound the interruption, and they do different jobs:

* **`BREAKING_MAX_PER_DAY = 5`** — how many breaking notifications one reader may receive in a day.
* **`RWE_BREAKING_TTL_HOURS` (6)** — how long an event stays deliverable. This is what makes the cap a
  *cap* rather than a *queue*: a story the cap held back yesterday expires instead of arriving
  tomorrow as news. Without the TTL the cap would only defer.

---

## 7. Operating it

| Variable | Service | Default | Meaning |
|---|---|---|---|
| `RWE_BREAKING_NOTIFICATIONS` | `api` | `0` (off) | The feature's on/off switch. Truthy = detection runs. |
| `RWE_BREAKING_MIN_PUBLISHERS` | `api` | `3` | Distinct outlets a story needs before it may interrupt a reader. |
| `RWE_BREAKING_TTL_HOURS` | `api` | `6` | How long an event stays deliverable. |
| `RWE_NOTIFY_EVENTS_WINDOW_HOURS` | `api` | `24` | Query bound on the event read (per-event `expires_at` is the real policy). |

All are read at call time: `deploy/ops/restart.sh api` applies a change with **no rebuild**.

**They are wired onto `api`, not `ingest`.** The `ingest` service runs the one-shot
`rss_ingest.py run`, which writes articles and exits without ever touching `story_service` — the
detection seam is `feed_service.FeedPoller.poll_once` and
`sources.MultiSourcePoller._post_cycle`, both of which run inside the engine container. Wiring the
flag onto `ingest` would turn nothing on.

**`environment:` is an explicit allowlist and this stack has no `env_file:`.** A variable absent from
the service never reaches the container, whatever `deploy/.env` says. That is why the switch is listed
in both compose files even though it defaults to off, and why
`deploy/deployment-rules.json` carries `api-breaking-notifications-switch` to keep it listed: an OFF
switch an operator cannot reach is the failure this guards against (the web tier's identity-recovery
lever shipped with exactly that bug).

### Turning it on

```bash
cd /opt/ih
$EDITOR deploy/.env                       # RWE_BREAKING_NOTIFICATIONS=1
deploy/ops/restart.sh api
docker exec deploy-api-1 printenv | grep RWE_BREAKING     # prove it reached the container
docker compose ... logs api | grep breaking_story_detected
```

Then confirm from the reader side: sign in, open the bell, and expect at most 5 breaking rows in a
day, each deep-linking to its story.

### Rolling it back

Set `RWE_BREAKING_NOTIFICATIONS=0` and `deploy/ops/restart.sh api`. Detection stops immediately. No
new events are recorded; already-recorded events stop being delivered once they pass their TTL, and
notifications already in a reader's inbox stay there (they describe something that really happened).
No rebuild, no revert, no data migration.

---

## 8. Verification

| Layer | Tests |
|---|---|
| Store (A1) | `tests/test_store_notifications.py` — UNIQUE-backed edge, concurrent losers, window/expiry filters, counts-by-day |
| Settings (A2) | `tests/test_settings_service.py`, `tests/test_api_fastapi.py` — per-leaf merge, API round-trip |
| Framework (A3a) | `tests/test_notification_service.py` — fan-out, budget, `discrete` in neither `EVENT_KINDS` nor the resolve set |
| Kind (A3b) | `tests/test_notification_service.py` — category gate, expiry, empty-title skip, cap |
| Boundary (A4) | `tests/test_notification_delivery.py` — fail-soft events, fail-closed counts, no resolve/collapse for discrete |
| Producer (A5) | `tests/test_story_events.py` — flag off, threshold, band, idempotence across cycles, both poller seams |
| Web (A6) | `web/lib/notifications.test.ts` — deep link, fallback for a payload with no usable `storyId`, escaping, other kinds unaffected |
| Deploy (A6) | `deploy/ops/validate-deployment.py` — the switch must stay wired on `api` in both stacks |
| Gating | `tests/test_notification_service.py` — per-channel paths, legacy kinds gating in-app only and failing closed elsewhere, unknown channel denied, channels gating independently, `evaluate(ctx)` byte-identical to `evaluate(ctx, "in_app")` |
| Email (C1) | `tests/test_email_digest.py` — consent conjunction, unsubscribe tokens, one mail per reader per week, bounce suppression, retry ladder, a rejected *sender* never suppressing a *recipient*; `web/lib/unsubscribe-public.test.ts` — the unsubscribe route stays outside the auth matcher |
| Preference | `web/lib/settings-diff.test.ts` — a two-level change ships one leaf, an identical rebuild is not a change |

Every commit was accepted against **mutation testing**, not just a green suite: the A6 resolver kills
all six mutants (drop `trim`, drop `encodeURIComponent`, resolve for every kind, never resolve, drop
the `typeof` guard, trim only in the guard); the gating refactor kills all seven (invert the category
check, path for an unknown channel, ignore the channel in `evaluate`, revert `gated_by`, ignore the
channel in resolution, collapse the push leaf onto `inApp`, disable the fan-out's category filter);
and the validator rule was verified by deleting the flag from the compose file and watching the rule
fail.

---

## 9. What Phase A deliberately does not do

* **No push.** No service worker, no VAPID keys, no subscription table, no delivery worker, no retry
  ladder. Evaluate-on-fetch reaches a reader on their next request, which is the right latency for
  in-app and the wrong one for push — Phase B inverts this to fan-out-on-write for that reason.
* **No scheduler and no queue.** The poll cycle is the only clock, and the store is the only state.
* **No second producer.** `notification_events` is general (`source_type` + `category`), but product
  announcements and digests are not wired to it yet.
* **Fan-out is per-reader-on-read**, so N readers each evaluate the same events. This is fine at Wave 0
  scale and is the first thing Phase B changes.
* **No retention policy on `notification_events`** — accepted, not overlooked. The table *is* the
  idempotency ledger, so deleting a row is precisely what would let a story be announced twice; and
  growth tracks news volume rather than traffic (order 10–50 rows/day at a 3-outlet floor). Any policy
  would have to retain rows far longer than the 6 h they are deliverable, which is a decision worth
  making deliberately — most naturally when a second producer starts writing to the same table.
  Recorded in `docs/STORAGE_LIFECYCLE.md` § "Not pruned, but not protected either".

---

*Related: `docs/BROWSER_PUSH_ARCHITECTURE.md` (Phase B — the frozen architecture for the push
channel, which adds no kind and changes nothing in this document),
`docs/STORAGE_LIFECYCLE.md` (notification retention), `docs/DEPLOYMENT_RUNBOOK.md` §
Breaking-story notifications, `docs/PRODUCTION_ENVIRONMENT.md` (engine feature flags),
`docs/IDENTITY_UPSERT_CONCURRENCY.md` §4 (why new code uses a second transaction rather than a
SAVEPOINT).*
