# Browser Push — Architecture Specification (Phase B)

**Status:** frozen. This document is the architectural contract for Phase B; it records the design
agreed across four reviews and the reasoning that produced it, including the alternatives that were
argued for and rejected.

It specifies **structure and contracts**, not construction. Where a decision was deliberately left to
implementation, it says so.

**Prerequisites, already shipped in Phase A** (`docs/NOTIFICATION_PLATFORM.md`): the notification
registry with per-kind lifecycles, `gate_path(kind, channel)` and the `notifications.categories.<c>.<inApp|push>`
preference matrix, the `notification_events` trigger table, and the in-app inbox. Phase B adds a
delivery channel. It adds no notification kinds and changes no notification semantics.

---

## 1. Goals

**G1 — Browser push notifications.** Deliver notifications to a reader's device via the Web Push
protocol when the application is not open, subject to the reader's explicit per-category consent.

**G2 — Reuse the existing notification platform.** Push is a *channel*, not a parallel system. The
same `NOTIFICATION_KINDS` registry decides what is due, the same `Notification` object describes it,
the same preference matrix gates it, and the same dedupe ledger stops it arriving twice. A kind
written before push existed becomes push-deliverable by a preference change, not a code change.

**G3 — No changes to notification generation semantics.** What is worth telling a reader is settled
before any channel is consulted. Specifically unchanged: the three lifecycles (`cadence` / `event` /
`discrete`) and their auto-resolve and collapse rules; per-kind daily caps; dedupe keys and the
four-level idempotency model; the level-versus-edge property of `notification_events`; and the
fail-open/fail-closed postures of the delivery boundary's reads.

### Non-goals

Phase B does not introduce quiet hours or any time-of-day policy (no per-user timezone exists — see
`docs/NOTIFICATION_PHASE_A_RETROSPECTIVE.md` §R5); does not change session, auth, or cookie shape;
does not migrate the six pre-push kinds from their flat per-kind toggles onto the category matrix;
and does not add a notification kind.

---

## 2. Architectural Principles

These four are the invariants. Every contract below is derived from them, and a proposal that
violates one is out of scope regardless of its other merits.

### P1 — The engine decides *what* to notify

Deciding is `notification_service.evaluate(ctx, channel)`: a pure function of a context assembled
from persisted producer state. It answers "which notifications are due for this reader on this
channel", and its answer is a list of `Notification` objects — `kind`, `dedupe_key`, `payload`,
`title_key`, `gated_by`. No channel participates in that decision beyond naming itself, and the
channel's only influence is which preference leaf `gate_path` reads.

### P2 — Delivery channels decide *how* to render

A `Notification` is data. Turning it into a dropdown row, an OS banner, or a MIME part is the
channel's business and no two channels do it alike. Channels share the *metadata* that describes a
kind (§3); they do not share rendering.

### P3 — The engine remains presentation-agnostic

The engine holds no display strings, no translations, and no mapping from a kind to prose. It emits
i18n **keys** and structured payloads. This is not stylistic: the engine has no i18n subsystem, and
introducing one would create a second catalog that `check:i18n` does not guard (§4).

### P4 — Service Worker rendering is deterministic and network-free before `showNotification()`

A service worker that receives a `push` event and does not call `showNotification()` causes the
browser to display its own generic message ("This site has been updated in the background"). **Every
failure in the worker's render path is therefore user-visible and worse than silence.**

This asymmetry — not payload size, not latency — is the governing constraint of the design. It means
the render path must not depend on the network, on authentication, on storage that may be absent, or
on the worker's understanding of the specific kind. Everything the worker needs to produce *something
correct* must already be in its bundle or in the message it just received.

### The precedence rule these produce

> **The payload is a fallback for what the device cannot derive — never an override of what it can.**

Applied consistently: the device's stored language beats the payload's language (§4); a worker that
understands a kind prefers its own deep link over the server's (§5). Local knowledge is fresher and
channel-appropriate; the payload exists to keep an *ignorant* worker correct.

---

## 3. Notification Metadata

### The shared contract

One pure, dependency-free table keyed by notification kind. It contains no icons, no components, no
DOM, no runtime imports, and is consumable by the React application, the service worker bundle, and a
bare `node --test` process alike.

| Field | Type | Meaning |
|---|---|---|
| `titleKey` | `string` | i18n key for the title. Per-kind, not per-notification. |
| `bodyKey` | `string \| null` | i18n key for the body, interpolated from the notification's `payload`. `null` ⇒ title only. |
| `href` | `string \| null` | The kind's static destination. `null` ⇒ informational, no navigation. |
| `deepLinkField` | `string \| null` | Name of the `payload` field that, when present and usable, replaces `href` with a per-notification destination (e.g. `storyId` → `/stories/{id}`). |

An **unknown kind** is not an error at this layer: the table simply has no row, and each consumer
applies its own fallback (below).

### Why metadata, and not a shared presentation function

The obvious alternative — one `notificationPresentation(kind)` both consumers call — was proposed
first and rejected on three grounds, in ascending order of force.

**The two consumers want different types under the same names.** React wants `icon` to be a
component. The Notification API wants `icon` to be a **URL to a raster image**. These are not one
concept in two dresses; they are different data with the same label, and a shared function would have
to return both or neither.

**Each consumer needs fields the other has no use for.** `showNotification()` takes `tag`, `badge`,
`actions`, `requireInteraction`, `silent`, `data` — none of which have a React counterpart. A
dropdown row needs relative timestamps, unread emphasis, and truncation rules the OS layer handles
itself. A shared function accretes the union, and every consumer pays attention to fields that are
inert for it.

**Decisively: the correct behaviour for an unknown kind is different per channel.** In the inbox, an
unknown kind degrades to a bell icon, a generic localized title, and *no navigation* — a safe,
inert row the reader can ignore. A push cannot be inert: it has already interrupted them, `href: null`
would produce a notification that does nothing when tapped, and the worker is obliged to render
something. Same input, two different correct answers. A shared *policy* would necessarily encode one
of them and be wrong on the other channel.

Sharing metadata keeps what genuinely is common — the kind→key mapping, which must never diverge or
the two channels describe the same event differently — and leaves per-channel judgement where it
belongs.

### Intentionally divergent rendering policies

| Concern | React inbox | Service Worker |
|---|---|---|
| Unknown kind | generic localized title, no body, **no navigation** | generic app-level title, **navigation to the app root or the payload's `href`** — a push must never be a dead tap |
| Icon | a component from the icon set | a URL to a raster asset in the worker's bundle |
| Grouping | none; rows accumulate in the list | `tag` set from the dedupe key, so the OS collapses a repeat rather than stacking it |
| Timestamps | relative ("2h ago"), recomputed on render | absolute, fixed at render; the OS owns the display |
| Truncation | CSS | the platform's own, uncontrollable |
| Missing body key | row renders title only | notification renders title only |

These differences are the specification, not drift. A future change that makes one channel behave
like the other should be justified against this table.

---

## 4. Localization

### Resolution order

The service worker resolves the rendering language in this order, taking the first that yields a
supported language (`en`, `es`, `fr`, `de`, `pt`):

1. **The reader's current language, read from browser storage.** The application publishes its active
   language to a store the worker can read whenever that language settles. This is authoritative
   because it is correct at *render* time.
2. **The `lang` field in the push payload.** A fallback only. It is the reader's language as known by
   the engine at *send* time.
3. **`en`, the platform default** (`DEFAULT_LANG` in `lib/i18n-core.ts`).

Each layer covers the layer below it, and both directions of failure are real. A push may sit in the
push service under its TTL, or wait for a device to come back online, so a language captured at send
time can be stale by the time it renders — which is why (2) does not outrank (1). Conversely, browser
storage can be empty: cleared site data, a restored device, a subscription that outlived the store —
which is why (1) alone is insufficient and (2) exists at all.

Today the reader's language lives in the engine's settings and is reflected into `<html lang>` by the
language provider. Neither is reachable from a service worker, so publishing it to a worker-readable
store is new infrastructure this design requires. The store is unspecified here; the contract is that
it is written by the application, read by the worker without a network call, and holds a value from
the supported set.

### The catalogs

`web/messages/{en,es,fr,de,pt}.json` remain the **single source of truth** for every display string,
including those rendered by the worker. `check:i18n` continues to enforce five-way key parity,
placeholder parity, no empty values, and no unused keys across all of them.

The worker bundles only the notification subset — 20 keys, **6,978 bytes across all five languages** —
so shipping all supported languages to every device is cheaper than any scheme for selecting one.

### Why server-side localization was rejected

Rendering on the engine looks like a small change and is not. Four inputs are needed to render a
notification, and two of them exist only in the web tier:

| Input | Lives in |
|---|---|
| `payload` (the story, the count, the streak) | engine — the stored notification body |
| the reader's language | engine — `settings.language` |
| the kind → `bodyKey` mapping | **web only** |
| the strings themselves | **web only** — `messages/*.json` |

The engine stores `title_key` and has never known that a weekly digest's body reads
"{reads} reads this week · {streakDays}-day streak." So "the engine renders" is not one change; it is
a migration of the presentation layer into the engine, plus a second copy of five catalogs that
`check:i18n` does not guard and that would drift the first time a string was edited on one side only.

Beyond the cost: the language is a property of the reader at *render* time, and a server renders at
*send* time. Server-side localization does not merely duplicate the catalogs, it fixes the language
at the wrong moment.

---

## 5. Push Payload

### Fields

The payload is a JSON object, camelCase on the wire (matching the existing HTTP contract). It is
encrypted end to end by the Web Push protocol; the push service cannot read it.

| Field | Type | Required | Purpose |
|---|---|---|---|
| `v` | `integer` | yes | Payload schema version. Governs the compatibility contract (§6). |
| `notificationId` | `integer` | yes | The engine's notification row id. Correlates a device-side render with the send, and identifies the row to mark seen on click. |
| `kind` | `string` | yes | The notification kind. The worker's key into the metadata table. |
| `payload` | `object` | yes | The kind's structured payload, **verbatim** as the engine stored it. Interpolated into `bodyKey`. |
| `dedupeKey` | `string` | yes | The notification's idempotency key; becomes the Notification `tag`, so the OS collapses a repeat instead of stacking it. |
| `lang` | `string` | yes | Fallback language (§4). Never an override. |
| `createdAt` | `ISO-8601 string` | yes | The notification's own timestamp — the moment it became true, not the moment it was sent. |
| `sentAt` | `ISO-8601 string` | yes | When the push was dispatched. With `createdAt`, lets a worker reason about staleness. |
| `href` | `string` | yes | Server-computed destination, an absolute in-app path. The **fallback** for a worker that does not know this kind; a worker that does know it derives its own from the metadata table and the payload. |

`titleKey` and `bodyKey` are deliberately **absent**: they are metadata, the worker already has them
for every kind it understands, and sending them would mean two sources of truth for the same mapping.
For a kind the worker does not understand, the keys would be useless anyway — it has no catalog entry
for them (§6).

### Size

The Web Push specification guarantees push services accept at least 4 KB of encrypted payload. A
typical notification here is 300–400 bytes, so the budget is not a live constraint — but it is a hard
failure when exceeded (the send is rejected outright), so the engine bounds what it emits: free-text
fields carried into a payload, principally story headlines, are truncated to a fixed limit before
send. A target of ≤ 1 KB plaintext leaves a wide margin for encryption overhead and for kinds whose
payloads are larger than today's.

### Why ID-only payloads were rejected

The alternative — send `{notificationId}` and have the worker fetch the rest — was compared on the
dimensions that distinguish them:

| | Full payload | ID-only |
|---|---|---|
| **Offline** | renders from what arrived | requires a live, authenticated fetch at the least reliable possible moment — a push often arrives exactly as connectivity returns |
| **Latency** | zero round trips | a blocking fetch inside the push event, before `showNotification()` |
| **Payload size** | ~300–400 B against a ≥ 4 KB floor; bounded by truncation | never a constraint |
| **Localization** | worker renders locally | worker renders locally — no difference, since the catalogs are on the device either way |
| **Cacheability** | can populate a cache the app later reads | can populate a cache the app later reads — no difference |
| **Rollout safety** | payload shape is fixed at send time | the server could tailor its response to the worker's version — but the worker must still *understand the kind* to render it, so this relocates the versioning problem rather than solving it |

Offline decides it, and P4 explains why decisively. ID-only makes a successful network round trip —
and a live session, since the fetch would need credentials — a *precondition* for avoiding the
browser's generic fallback message. It puts a dependency on the least reliable resource in the system
directly into the one code path whose failure is guaranteed to be seen by the reader.

The one advantage ID-only holds, rollout safety, is answered by §6 without importing a network
dependency.

`notificationId` is nonetheless carried, because it is nearly free and buys two things: the click
handler can mark the notification seen, and the engine's send log can be correlated with what a device
actually rendered — a partial answer to the observability cost that device-side rendering imposes.

---

## 6. Compatibility Contract

Server and worker versions drift by construction: the server updates on deploy, a service worker
updates when the browser next fetches it and the old one releases control. **A newer server talking to
an older worker is the normal case, not an edge case**, and the contract is written from that
assumption.

### Guarantees the server makes

- **Additive change only.** New payload fields may be added at any time. An existing field is never
  removed, renamed, repurposed, or given a new meaning under its old name.
- **`v` increments only for a change an old worker cannot safely ignore.** Adding a field does not
  increment it.
- **Every payload is renderable by a worker that understands nothing about the kind**: `href`,
  `dedupeKey`, `createdAt` and `lang` are present regardless of kind, so a generic render is always
  possible.
- **No field is a promise about presentation.** The server never sends prose, never sends a
  translated string, and never sends a rendering directive.

### Guarantees the worker makes

- **`showNotification()` is always called.** No input — unknown kind, unknown fields, higher `v`,
  malformed payload, missing catalog entry — may result in no notification. The browser's generic
  message is treated as a defect.
- **Unknown fields are ignored**, never fatal.
- **A raw i18n key is never displayed.** If a key resolves to nothing, the worker falls back to
  generic app-level copy.
- **Unknown kind** ⇒ generic app-level title, no body, navigation to the payload's `href`.
- **Higher `v` than the worker understands** ⇒ the same generic render. The worker degrades; it does
  not refuse.

### Forward compatibility (old worker, new server)

A worker meeting a kind that did not exist when it was built renders the generic form and navigates
via the server-computed `href`. The reader gets a correct, tappable, correctly-localized-in-frame
notification that names the application rather than the specific event. This is the designed
degradation, and it is the same shape as the inbox's unknown-kind fallback in Phase A.

### Backward compatibility (new worker, old server)

A worker must not require any field a previously deployed server did not send. Fields introduced after
a worker's contract version are read defensively, with the worker's own defaults when absent. This
matters on rollback: reverting the engine must not break workers already installed on devices, which
cannot be rolled back.

### What is not guaranteed

Presentation stability across worker versions. Two devices on different worker versions may render the
same notification differently — one specifically, one generically. That is an accepted consequence of
device-side rendering and the reason the metadata table is small and slow-moving.

---

## 7. Worker Responsibilities

"Worker" here means the **server-side sender** — the engine-side component that turns due
notifications into Web Push requests. (The browser-side service worker's obligations are §3, §4 and
§6.) Phase A's evaluate-on-fetch reaches a reader on their next request; push requires the inverse,
and this is the component that inverts it.

### Subscription lookup

Push subscriptions are per **device**, not per reader: one reader may hold several, and each carries
its own endpoint and encryption keys. Fan-out-on-write must answer "which subscriptions should receive
this notification", which is a query Phase A never had to make — the reader's own request supplied the
identity.

Reader preferences are stored as an opaque JSON blob, so the naive form of that query is a full scan
with a JSON parse per row. The contract is therefore that **the subscription record carries the
per-category push flags in indexed columns**, maintained in step with the settings that own them.
Settings remain authoritative; the subscription's copy is a query accelerator and must be treated as
such — including that a stale copy is corrected by, never allowed to contradict, `gate_path`.

The gate itself is unchanged and unduplicated: `gate_path(kind, "push")` evaluated against the
reader's normalized settings, the same pure function the inbox uses, fail-closed on a missing path
and on an unknown channel.

### Worker pool

Sending is bounded-concurrency work performed **off the ingest poller's thread**. The poller's job is
ingestion; a fan-out that blocks it converts a notification-delivery problem into a corpus-freshness
problem. Concurrency is bounded so a large fan-out cannot exhaust connections or memory, and the whole
fan-out for one notification is itself bounded, so an unbounded subscription list cannot occupy the
sender indefinitely.

### Timeouts

Every send carries a deadline. This mirrors the identity-recovery lesson (`docs/SESSION_IDENTITY_RECOVERY_DESIGN.md`
§5b): an unbounded call to a remote service is not a slow path, it is a wedged one, and a wedged
sender is indistinguishable from a broken feature. A send that exceeds its deadline is a retryable
failure, not a terminal one.

### Retries

Failures are classified before they are retried, because the two classes need opposite treatment:

- **Retryable** — timeouts, connection failures, `429`, `5xx`. Retried a bounded number of times with
  backoff, honouring `Retry-After` where the push service supplies it.
- **Terminal** — `400` (malformed request), `403` (VAPID mismatch), `413` (payload too large). Retrying
  cannot succeed; these are logged loudly because each indicates a defect on our side rather than a
  transient condition.
- **Terminal and pruning** — `404`, `410` (below).

Retries are safe because delivery is idempotent: a `UNIQUE(notification_id, channel, subscription_id)`
ledger is the third of the platform's four idempotency levels, and the Notification `tag` derived from
`dedupeKey` is the fourth, collapsing any duplicate that still reaches a device.

### Pruning expired subscriptions

A `410 Gone` (and `404 Not Found`) from the push service means the subscription is permanently
invalid: the reader revoked permission, cleared site data, or the browser rotated it. The row is
deleted.

This is not housekeeping. Without it, dead subscriptions accumulate, every subsequent fan-out pays to
attempt them, and the delivery metrics fill with failures that describe nothing. Pruning is what keeps
the subscription table a description of reachable devices.

### Metrics

The send path is the first part of this system a reader cannot see failing, so it is the first that
needs to be countable. At minimum: notifications considered, sends attempted, sends succeeded, sends
failed by classification, subscriptions pruned, and fan-out duration. Interpretation matters more than
collection — a rising prune rate is ordinary attrition; a rising `403` rate is a credential defect; a
rising timeout rate is the push service or our egress.

Log lines carry the notification id and the kind, so a device-side render can be traced back to a
send, which is the mitigation for rendering where we cannot observe.

---

## 8. Explicitly Rejected Designs

Each entry records the argument that defeated it and the condition under which it would deserve
reconsideration. A rejection with no reopening condition is a rejection nobody can revisit honestly.

### Server-side rendering

**Rejected because** the engine holds neither of the two web-tier-only inputs rendering requires (the
kind→`bodyKey` mapping and the catalogs), so adopting it means migrating the presentation layer into
the engine and maintaining a second set of five catalogs outside `check:i18n`'s guarantee. It also
fixes the reader's language at send time, when language is a render-time property (§4).

**Would deserve reconsideration if** a channel arrives that has no device-side renderer at all — email
is exactly this (§9). Note that even then the conclusion is a *renderer that holds catalogs*, not an
engine that holds strings.

### Shared presentation functions

**Rejected because** the two consumers need different types under the same field names, need
disjoint sets of extra fields, and — decisively — require *different* correct behaviour for an unknown
kind, which a shared function would have to pick one of (§3).

**Would deserve reconsideration if** the channels' rendering models converged, which the direction of
travel (mobile push, email) makes less likely rather than more.

### ID-only payloads

**Rejected because** they make a successful authenticated network fetch a precondition for avoiding the
browser's generic fallback message, placing the least reliable resource in the system inside the one
path whose failure the reader is guaranteed to see (P4, §5).

**Would deserve reconsideration if** a notification kind's payload could not be bounded to a safe size
— in which case the right answer is likely a hybrid (render from the payload, fetch enrichment
*after* `showNotification()`), not a reversal.

### Duplicated localization catalogs

**Rejected because** `check:i18n` guards one copy. A second copy is not a second source of truth for
long; it is a source of drift with no gate, and the first symptom is a reader seeing different words
for the same event in the inbox and on their lock screen.

**Would deserve reconsideration if** a catalog copy could be generated from the authoritative one as a
build artifact and verified by the same gate — a derived copy is not a duplicate.

---

## 9. Future Extensions

The shape this design settles on — *engine decides, metadata is shared, each channel renders* —
extends along one axis (new channels) and one dimension (new kinds), independently.

### Email

Email is the interesting case, because it has no device-side renderer: an email client cannot execute
the metadata table. A server must render it.

That does **not** overturn P3. The principle is that the *engine* is presentation-agnostic, not that
rendering never happens on a server. Email adds a renderer that holds the catalogs and consumes the
same metadata table, producing MIME rather than DOM or `showNotification()` options. Because the
catalogs live in the web tier, the natural home for such a renderer is the web tier, invoked by the
engine — which keeps the engine string-free and keeps one copy of the catalogs.

That the metadata table serves a third consumer whose output is neither DOM nor a notification object
is the clearest vindication of §3: had presentation been shared as a function, email would have needed
a third incompatible return shape bolted onto it.

Email also inherits the whole platform unchanged: `gate_path(kind, "email")` needs one entry in the
channel→settings-leaf map and one leaf per category; the delivery ledger's `channel` column already
distinguishes it; and every existing kind becomes email-deliverable without being touched.

### Mobile push

FCM and APNs differ from Web Push in transport, credentials, and payload envelope — not in model. The
device renders, the payload is self-contained, the metadata table is the same, and §6's compatibility
contract applies with more force, since a mobile app's update cadence is slower and controlled by an
app store rather than by a page load. The sender's responsibilities in §7 transfer directly: bounded
concurrency, deadlines, classified retries, and pruning on the platform's equivalent of `410`.

### Additional notification kinds

A new kind is a row in the notification registry (what makes it due, its lifecycle, its cap, its
category) and a row in the metadata table (its keys, its destination). If it is triggered by a global
occurrence rather than by one reader's state, it also needs a producer writing to `notification_events`
under its own `source_type` and category.

No channel changes. Existing service workers on devices render it generically until they update, which
is the compatibility contract working as designed rather than a deployment to coordinate.

---

*Related: `docs/NOTIFICATION_PLATFORM.md` (Phase A design and operations),
`docs/NOTIFICATION_PHASE_A_RETROSPECTIVE.md` (the review that produced R3–R6, the findings this
specification answers), `docs/SESSION_IDENTITY_RECOVERY_DESIGN.md` §5b (the timeout precedent).*
