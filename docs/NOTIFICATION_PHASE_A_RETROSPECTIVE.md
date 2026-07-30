# Phase A retrospective — read before starting Phase B

Written after Phase A shipped (A1–A6) and before browser push begins, from a re-reading of the code
rather than from memory of writing it. Two of its findings were acted on immediately and are marked
**done**; the rest are decisions Phase B inherits.

Design and operating instructions live in `docs/NOTIFICATION_PLATFORM.md`. This file records what was
*wrong or provisional* and why each call was made.

---

## 1. Acted on

### R1 — the preference had no UI ✅ done

`notifications.categories.breaking.inApp` existed end to end in the engine — default, normaliser,
`SettingsModel`, `SettingsUpdateModel` — and nowhere in the web tier: `Settings.notifications`
declared four booleans and no `categories`, and the settings page rendered four toggles, none of them
breaking news. The default is `inApp: true`, so the day `RWE_BREAKING_NOTIFICATIONS=1` was set,
readers would have received breaking notifications with **no way to turn them off**.

Not an incident — the flag is off — but a hard prerequisite for ever setting it, and a bigger one for
push, where a default matters far more.

What was *not* wrong, and was checked rather than assumed: the round-trip was never lossy.
`settings_service.update` normalises `[defaults, stored, patch]` and re-saves the whole object, so a
web patch omitting `categories` preserved them. A missing control, not data loss.

Fixed by *Give the reader a switch for breaking news*, which also made `diffSettings` recurse two
levels — flipping one channel had been about to ship the entire 4×2 matrix, and the engine
deep-merges, so restating unchanged siblings silently overwrites another device's change.

### R2 — the channel was baked into the kind's gate ✅ done

`NotificationKind.setting_path` was a single string and A3b set `breaking_story`'s to the literal
`notifications.categories.breaking.inApp`. That reads as a preference and behaves as a decision: a
reader with `inApp: false, push: true` produced **no `Notification` at all**, so there would have been
nothing for a push channel to send. No amount of Phase B work could have fixed it downstream.

Fixed by *Let a reader want breaking news in the app but not on their phone*: a kind declares its
`category`, `gate_path(kind, channel)` derives the per-channel path, and `evaluate(ctx, channel)`
defaults to in-app so every existing call is byte-identical. Small now, expensive once three or four
category kinds exist — which is the whole argument for doing it before Phase B rather than during it.

---

## 2. Carried forward — decisions for Phase B

### R3 — the `Channel` protocol is unexercised

`InAppChannel` is referenced only by tests. `materialize_notifications` writes
`dataclasses.asdict(n)` directly, so the seam Phase B was meant to extend has never carried traffic,
and `render(notification) -> dict` returning an i18n key is probably the wrong signature for push,
which needs a subscription, a TTL, an urgency and a topic.

**Recommendation: do not extend it speculatively.** Design the real abstraction against two real
channels in Phase B's first commit, or delete it. An unexercised seam is a guess, and the guess is
already visible in the signature.

### R4 — rendering is deferred to the client, and the catalogs live only in the web tier

Verified: there is no engine-side i18n of any kind. In-app works because the browser has
`web/messages/*.json` and `NotificationModel.titleKey` is passed straight through. A Web Push payload
has no such luxury — the service worker gets only what the server sends.

Three ways out, and **the choice decides which tier the push sender lives in**, which is the largest
structural decision in Phase B:

1. the service worker renders from `{titleKey, bodyKey, payload}` against a catalog bundled at build
   time — consistent with "rendering is a channel's job", keeps the engine i18n-free *(recommended)*;
2. the push sender lives in the web tier, which already has the catalogs and would naturally hold the
   VAPID keys;
3. the engine gains a copy of the catalogs — worst: two sources of truth, and `check:i18n` guards only
   the web copy.

### R5 — Phase A never had to think about time of day; push does

Evaluate-on-fetch means an in-app notification waits for the reader. A push arrives at 3am. There is
**no per-user timezone anywhere in the system** — the cap, the reading streak and every other bucket
use UTC days, so this is a consistent convention rather than an inconsistency, but quiet hours would
need a new stored field and a new product decision. New requirement, not Phase A debt.

### R6 — "who should be notified" is a query Phase A never needs

Fan-out-on-read takes the user id from the request. Fan-out-on-write must enumerate subscribers with
push enabled for a category, and settings are stored as an opaque JSON blob — so that is a full scan
plus a JSON parse per event. Fine at Wave 0; the first thing that needs an index later. Since
`push_subscriptions` has to exist anyway, carrying a denormalised per-category flag on it solves this
for free, and is worth doing in the same commit that creates the table.

---

## 3. Accepted, documented, not fixed

Both are recorded in `docs/NOTIFICATION_PLATFORM.md` §3 with the reasoning; summarised here so a
future reader finds them from either direction.

| | Finding | Why it stays |
|---|---|---|
| R7 | The dedupe ledger and the display history are the same table, so `prune_notifications` erases idempotency. | Unreachable under the current caps (200 rows inside a 6 h TTL is impossible). The fix, when a higher-volume kind arrives, is a ledger that outlives the row — not a bigger `keep`. |
| R8 | The daily cap is very nearly, but not exactly, a hard bound under concurrent fetches. | Deterministic event-derived dedupe keys make the two requests collide, so an overrun needs a new event landing between two context builds and costs one or two rows. Locking the read would cost more than it prevents. |

Also corrected while the code was fresh: `DISCRETE_KINDS` claimed the delivery boundary used it to
exclude discrete kinds explicitly. The boundary asks `EVENT_KINDS`, which is an **allowlist** — the
exclusion therefore holds for a future mode too. The docstring now describes what it is: an assertion
surface for the tests.

---

## 4. Is the platform generic enough for more kinds?

**Yes for the framework.** The registry row, `mode`, `max_per_day`, `fanout`, the event table
(`source_type` + `category` are producer-agnostic), the category × channel settings matrix and the
store accessors all carry a second kind unchanged. A new *state* kind is one row; a new *event* kind
is one row plus a producer plus a fan-out function.

**No for the event→notification adapter, deliberately.** `_breaking_fanout` hard-codes its payload
shape, its empty-title rule and its expiry check. A `_category_fanout(category, payload_builder)`
factory is the obvious extraction and it is **not** being done yet: with one consumer it would be
guessing at the second one's requirements (does a product announcement expire? carry a per-item title?
deep-link?). Extract it when a second kind makes those differences facts rather than predictions.

One constraint worth naming: **`title_key` is per-kind, not per-notification.** Every breaking story
shares one title and the variation lives in the body. Fine now; a future kind that needs two titles
can relax it by letting the fan-out pair carry a title key. Don't pre-build that either.
