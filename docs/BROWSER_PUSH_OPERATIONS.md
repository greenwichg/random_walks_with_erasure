# Browser Push — Deployment, VAPID keys, and Operations

Operational companion to [`BROWSER_PUSH_ARCHITECTURE.md`](BROWSER_PUSH_ARCHITECTURE.md), which is the
frozen design. This document covers what an operator does: generating keys, turning the feature on,
verifying it, rolling it back, and reading its failures.

**Current state: Phase B4 — registration, delivery, retries, and the operational surface.** A browser can be asked for
permission and register a subscription (B1); the engine **sends** (B2) — a worker hangs off the
ingestion poller, fans an event out to every consenting device, records every attempt in a delivery
ledger, and prunes endpoints a push service declares gone; and a failure that could succeed later is
now **tried again** (B3) on an exponential backoff that survives a restart, because the schedule is a
column rather than a timer. B4 adds what an operator needs when it goes wrong: a per-push-service rate
limit (§7.2), counters split by failure classification (§7.3), a graceful stop, and a startup report
of what the previous process left behind (§7.4).

**Two switches, not one.** `RWE_PUSH_ENABLED` governs *registration*; `RWE_PUSH_DELIVERY` governs
*sending*. They are separate on purpose: an operator can let readers subscribe, watch the subscription
table fill, and only then turn on the switch that first puts a notification on somebody's lock screen.

Every action is logged (§6 for registration, §7 for delivery), and a rollback closes each half
independently without stranding the readers who already registered (§4).

---

## 1. Configuration

All eight variables live on the **`api`** service, in both compose files, and are read at call time —
so every change below is a `deploy/ops/restart.sh api`, never a rebuild.

| Variable | Default | Governs | Meaning |
|---|---|---|---|
| `RWE_PUSH_ENABLED` | `0` (off) | registration | Off ⇒ **registration** answers `503`; reading and deleting a subscription stay open (§4). |
| `RWE_VAPID_PUBLIC_KEY` | *(empty)* | registration | Served to browsers, which subscribe against it. Public by construction. |
| `RWE_PUSH_MAX_DEVICES` | `10` | registration | How many devices one reader may hold. Past the cap the least-recently-registered is dropped. `0` = unbounded. |
| `RWE_PUSH_DELIVERY` | `0` (off) | **delivery** | The send switch. Off ⇒ the worker returns immediately and nothing is ever handed to a push service. Accepts `1`/`true`/`yes`/`on`. |
| `RWE_VAPID_PRIVATE_KEY` | *(empty)* | **delivery** | Signs sends. Without it delivery stays off however `RWE_PUSH_DELIVERY` is set. |
| `RWE_VAPID_SUBJECT` | *(empty)* | **delivery** | The `mailto:` or `https:` contact given to push services. Also required to send. |
| `RWE_PUSH_SEND_TIMEOUT_MS` | `10000` | delivery | Per-send deadline in **milliseconds**. A zero or unparseable value falls back to the default rather than disabling the deadline. |
| `RWE_PUSH_MAX_SENDS_PER_SECOND` | `10` | delivery | Sends per second **per push service** (§7.2). `0` disables the limit. Unparseable falls back to the default — a typo must not read as "no limit". |

**Delivery needs all three of its variables.** The switch alone, or the switch with only one key, sends
nothing — and does so silently, because a missing key on a background thread is a reason not to run
rather than an error to raise. §3 has the command that proves which state you are in.

`deploy/deployment-rules.json` enforces four things: both switches and the rate limit must stay wired
on `api` whether or not they are set (a control an operator cannot reach during an incident is the
failure being guarded against — `environment:` is an explicit allowlist with no `env_file:` behind
it), and turning registration on requires all three key variables — with the private key
**interpolated from `deploy/.env`**, never written into a compose file.

**Both halves matter.** The engine reports push as available only when the switch is on *and* a public
key is present. A deployment with one but not the other reports the feature off, which is deliberate:
half-configured is unavailable, not half-live.

---

## 2. Generating the VAPID key pair

VAPID keys are an ECDSA P-256 pair, encoded base64url. The public key is the uncompressed point (65
bytes, first byte `0x04`); the private key is the 32-byte scalar.

This recipe needs nothing but `openssl` — no new dependency in the image, and nothing to install on
the host:

```bash
# 1. The pair, as a PEM you then throw away.
openssl ecparam -name prime256v1 -genkey -noout -out vapid.pem

# 2. Public key -> base64url (65 bytes, starts 0x04)
openssl ec -in vapid.pem -pubout -outform DER 2>/dev/null \
  | tail -c 65 | base64 | tr '/+' '_-' | tr -d '=\n'; echo

# 3. Private key -> base64url (32 bytes)
openssl ec -in vapid.pem -outform DER 2>/dev/null \
  | tail -c +8 | head -c 32 | base64 | tr '/+' '_-' | tr -d '=\n'; echo

# 4. Destroy the PEM — the two strings above are the only copies you need.
shred -u vapid.pem 2>/dev/null || rm -f vapid.pem
```

Sanity check before you trust them: the public key should be **87 characters** and begin with `B`
(base64url of a leading `0x04`), and the private key **43 characters**. A public key of any other
length will be rejected by the browser at `subscribe()` time, which surfaces as "push is broken"
rather than "the key is malformed" — hence the check here.

Then in `deploy/.env`:

```
RWE_PUSH_ENABLED=1
RWE_VAPID_PUBLIC_KEY=<the 87-character string>
RWE_VAPID_PRIVATE_KEY=<the 43-character string>
RWE_VAPID_SUBJECT=mailto:ops@hidden-view.com
```

**The pair must stay together.** A public key from one generation with a private key from another
produces sends every push service rejects, and the error arrives asynchronously at send time — long
after the misconfiguration.

---

## 3. Turning it on

```bash
cd /opt/ih
$EDITOR deploy/.env                       # the registration variables above
deploy/ops/restart.sh api                 # read at call time — no rebuild
docker exec deploy-api-1 printenv | grep -E 'RWE_PUSH_ENABLED|RWE_VAPID'   # prove it landed

# From INSIDE the container. The AWS override unpublishes port 8000 — the engine is only on the
# private Docker network — so `curl http://127.0.0.1:8000/...` on the host connects to nothing and
# `curl -s` prints nothing at all, which reads as "the endpoint returned empty" rather than "there
# was nobody to ask". This is the same shape `deploy/ops/smoke-test.sh` uses, and urllib is stdlib
# so it needs nothing installed in the image.
docker exec deploy-api-1 python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/push/config').read().decode())"
# expect {"enabled":true,"publicKey":"B..."}
```

`printenv` showing nothing means the variable never reached the container — `environment:` is an
explicit allowlist and this stack has no `env_file:`, so a value in `deploy/.env` alone does not
suffice. That is the failure this repo has hit twice; the validator now guards it, but the check costs
nothing.

Then from a browser, signed in:

1. **Settings → Notifications** shows two push controls once the config endpoint reports the feature
   available. If neither appears, go back to §1.
   - **"Breaking news on your devices"** — the account-level preference for the push *channel*. It is
     **off by default** and travels with the reader to every device they own. Nothing is delivered
     until it is on, however many devices are registered.
   - **"Push notifications on this device"** — the per-device control. It prompts for permission and
     registers a subscription; it is not part of the Save flow.
2. Turn on **both**. They answer different questions — *what may be sent* and *where it may go* — and
   a device registered without the channel preference receives nothing, silently. That combination
   shipped once and is the reason this step now spells it out.
3. Confirm the engine stored it:
   ```bash
   docker exec -i deploy-api-1 python - <<'PY'
   import sys; sys.path.insert(0, '/app/examples')
   import store, sqlalchemy as sa
   with store.Store().session() as s:
       for row in s.execute(sa.text(
           "select id, user_id, substr(endpoint,1,60), user_agent, updated_at "
           "from push_subscriptions order by id desc limit 20")).all():
           print(row)
   PY
   ```

Expect one row per device. A reader with a laptop and a phone has two, and that is correct — a
subscription is a device, not an account.

### Then turning delivery on

Registration and delivery are switched separately, and the order matters: register at least one device
of your own first, so the first thing the pipeline ever sends goes to a device you are holding.

```bash
$EDITOR deploy/.env                       # RWE_PUSH_DELIVERY=1
deploy/ops/restart.sh api
docker exec deploy-api-1 printenv | grep -E 'RWE_PUSH_DELIVERY|RWE_VAPID_(PRIVATE|SUBJECT)'
```

Prove the engine agrees it can send — this is the one check that distinguishes "off" from
"misconfigured", which the logs cannot, because a deployment that cannot send says nothing at all:

```bash
docker exec -i deploy-api-1 python - <<'PY'
import sys; sys.path.insert(0, '/app/examples')
import push_delivery
print("enabled:", push_delivery.enabled())
print("can send:", push_delivery._sender() is not None)
print("timeout (s):", push_delivery._timeout_seconds())
PY
```

`enabled: True` with `can send: False` means a VAPID variable is missing — go back to §1. Delivery then
runs on the ingestion poller's cycle: the next breaking-story event fans out within one cycle, and
`push_run_complete` (§7) is the line that says it happened.

---

## 4. Rolling it back

Two independent rollbacks. **Prefer the delivery switch** — it is the one that stops notifications
arriving, and it leaves every registered device in place to resume without a reader touching anything:

```bash
$EDITOR deploy/.env                       # RWE_PUSH_DELIVERY=0
deploy/ops/restart.sh api
```

The worker then returns immediately on every cycle. Nothing is queued while it is off, so turning it
back on does not produce a flood — events that expired meanwhile are simply not sent, which is correct:
a three-day-old "breaking" is not breaking.

Turning off **registration** as well is the wider rollback:

```bash
$EDITOR deploy/.env                       # RWE_PUSH_ENABLED=0
deploy/ops/restart.sh api
```

**Registration closes; reading and deletion stay open.** That asymmetry is deliberate:

| Route | While `RWE_PUSH_ENABLED=0` |
|---|---|
| `POST /api/me/push/subscriptions` | `503` — no new device may register |
| `GET /api/me/push/subscriptions` | **works** — a reader can see what is registered in their name |
| `DELETE /api/me/push/subscriptions` | **works** — a reader can remove a device |

Stored subscriptions are **kept**: they are devices readers explicitly connected, and turning the
feature back on should not ask everyone to opt in again. That is exactly why the way out has to keep
working while the way in is shut — gating all three would leave a reader with a registration they
could neither see nor remove, and no operator path short of a SQL statement.

The Settings control follows the same rule. A device that is still registered shows a **paused** row —
switch on, one direction of travel, copy explaining push is currently unavailable — so the open
`DELETE` is actually reachable. A device that is *not* registered sees no control at all, because
there would be nothing for it to do.

**`RWE_PUSH_ENABLED=0` does not stop sending.** The two switches are independent, and this is the one
place that independence can surprise you: with registration off and delivery still on, no *new* device
can register but every device already registered keeps receiving. If the reason you are rolling back is
that readers are getting notifications they should not, **`RWE_PUSH_DELIVERY=0` is the switch you
want** — on its own, or before this one.

With delivery off, browsers keep their own subscription objects, which is harmless: nothing sends, so
nothing arrives.

**To also drop the stored subscriptions** (a decision, not a rollback step — readers must re-enable
afterwards):

```bash
docker exec -i deploy-api-1 python - <<'PY'
import sys; sys.path.insert(0, '/app/examples')
import store, sqlalchemy as sa
with store.Store().session() as s:
    print("deleted:", s.execute(sa.text("delete from push_subscriptions")).rowcount)
PY
```

---

## 5. Rotating the key pair

Generate a new pair (§2), replace all three values, restart `api`.

**Every existing subscription becomes undeliverable.** A subscription is bound to the key it was
created against, and a push service rejects sends signed by a different one.

The repair is automatic, and this is exactly what it does. On the reader's **next page load** (any
page — the repair runs from the app shell, not from Settings) the web tier compares the key its
subscription declares against the key the server now serves
(`web/lib/push.ts::subscriptionMatchesKey`). On a mismatch, and **only** when that device already held
a subscription and notification permission is still granted, it:

1. unsubscribes the stale subscription in the browser,
2. subscribes again against the new key — with no permission prompt, because consent already exists,
3. registers the new endpoint with the engine, and
4. only then deletes the retired endpoint's row, so an interruption leaves the device reachable
   rather than unreachable.

A reader who never enabled push is not subscribed by a rotation, and a reader who revoked permission
is not re-subscribed behind the revocation — both are guarded explicitly
(`push.ts::shouldRepairSubscription`).

**The window that remains** is between the rotation and each reader's next page load. Devices are
unreachable for that period and there is no server-side signal for it, because a rotation produces no
failed send until something is sent. A reader who does not return for a week has a device that stays
dark for a week. Rotate when there is a reason to, not on a schedule, and expect the subscription
table to churn afterwards — one row replaced per returning device.

---

## 6. Logging — registration

One structured JSON line per event on the engine's logger, correlatable by `requestId` like every
other line. **Endpoints and device keys never appear.** An endpoint is a capability and identifies a
specific browser install; logs are shipped, rotated and read by people, so a line carries
`endpointDigest` — the first 12 hex characters of the endpoint's SHA-256 — which is enough to
correlate a registration with the deletion of the same device and nothing else.

| Event | Level | Fields | What it tells you |
|---|---|---|---|
| `push_subscription_created` | INFO | `userId`, `subscriptionId`, `endpointDigest`, `reason` | A new device registered. |
| `push_subscription_updated` | INFO | same | The same browser re-registered — a key rotation repair, or the browser rotating its own subscription. Read `reason`. |
| `push_subscription_reassigned` | **WARNING** | same + `previousUserId` | One browser's endpoint moved between accounts. Normal on a shared machine; a rising rate is worth understanding. |
| `push_subscription_deleted` | INFO | `userId`, `endpointDigest`, `reason`, `removed` | A device was unregistered. `removed:false` means it was already gone or was never that reader's. |
| `push_subscription_evicted` | INFO | `userId`, `endpointDigest`, `cap` | The device cap dropped a reader's quietest device. Seeing these regularly means `RWE_PUSH_MAX_DEVICES` is too low for how people actually use the product — raise it rather than wonder why push is flaky. |
| `push_subscription_claim_refused` | **WARNING** | `userId`, `endpointDigest`, `reason` | Someone submitted an endpoint belonging to another reader **without the subscription's own secret**. No real browser can produce this, so it is a replayed endpoint or a client bug. |
| `push_subscription_rejected` | WARNING | `path`, `fields`, `errors` | A browser produced a subscription the engine refused. **Field names only** — the submitted value is an endpoint or a key. |
| `push_registration_rejected` | INFO | `userId`, `reason`, `enabled`, `configured` | A browser tried to register while push was off or unconfigured. |

### `reason`, and the question it answers

Every registration and deletion carries why it happened, from a closed set — a client cannot write
arbitrary strings into the log:

- **`user`** — a reader used the Settings control.
- **`repair`** — the client found its subscription bound to a retired VAPID key and re-subscribed (§5).
- **`worker`** — the browser rotated the subscription and the service worker re-registered it.
- **`repair_retire`** — the deletion of the endpoint a repair replaced.

This is what makes a rotation auditable. After changing the key pair, `push_subscription_updated`
with `reason=repair` and `push_subscription_deleted` with `reason=repair_retire` should appear in
pairs as readers return; the count of distinct `userId`s in those lines is how many devices have
actually healed. Without it every registration looks alike and "did the rotation work?" has no answer
short of querying the table.

### The client half — reconciliation, in the browser console

The events above are everything the **engine** sees, and there is a blind spot in them by
construction: a repair that never reaches the engine leaves no engine-side trace. That is not
hypothetical, it is how the device-goes-dark case presents — the browser holds a subscription, the
engine holds no row, and the engine's log is silent because nothing asked it anything.

So the client logs too, in the same one-JSON-object-per-line shape, via `console.warn`
(`web/lib/push-client.ts`). These appear in the reader's DevTools console, not in `docker logs`, and
are what to ask for when a specific device will not register. **Endpoints are truncated to their last
twelve characters** — enough to tell two lines about the same device apart, not enough to deliver to.

| Event | Fields | What it tells you |
|---|---|---|
| `push_repair_started` | `cause`, `endpoint` | Reconciliation decided this device needs re-registering. `cause=key_rotated` is a VAPID rotation; `cause=unknown_to_server` is a row the engine pruned on a `410`. |
| `push_repair_succeeded` | — | The device is registered again. Pairs with a `push_subscription_created` engine-side. |
| `push_repair_failed` | `failure`, `status`, `error`, `endpoint` | It tried and did not get there. `no_service_worker` = registration failed; `unserializable` = the browser gave a subscription with no keys; `rejected` + `status` = the engine refused the POST; `threw` + `error` = a platform call raised, which is the common one on a browser holding a subscription it will not replace. |
| `push_subscribe_failed` | `failure`, `status` | The reader-initiated path — the Settings toggle — failed. Same `failure` vocabulary. |

**The silent case is silent on purpose.** Reconciliation runs on every authenticated page load, and
on almost all of them there is nothing to repair. Only anomalies print, so a console with no
`push_repair_*` lines means the device and the engine already agree — not that nothing ran.

**Where reconciliation runs.** `components/push/push-reconciler.tsx`, mounted in the authenticated
app shell (`app/(app)/layout.tsx`) — every signed-in page, once per app load. It was originally
reached only through the settings toggle's hook, which meant a device pruned by a `410` stayed dark
until the reader happened to open Settings; both failures it repairs happen while they are somewhere
else entirely. It prompts for nothing and cannot subscribe a reader who never opted in.

### Reading the volumes

```bash
COMPOSE="docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env"
$COMPOSE logs api | grep -o '"event":"push_[a-z_]*"' | sort | uniq -c    # the shape of the traffic
$COMPOSE logs api | grep push_subscription_reassigned                    # shared devices changing hands
$COMPOSE logs api | grep push_subscription_rejected                      # browsers we are refusing
$COMPOSE logs api | grep 'push_subscription_updated.*"reason":"repair"'  # rotation repairs landing
$COMPOSE logs api | grep push_subscription_claim_refused                 # endpoints being replayed
$COMPOSE logs api | grep push_subscription_evicted                       # is the device cap biting?
```

A steady trickle of `created` with occasional `deleted` is ordinary. A burst of
`push_registration_rejected` means browsers are still trying against a switched-off deployment — a
rollback readers have not seen yet, or a half-configured deploy. A burst of
`push_subscription_rejected` means something about the client changed, and it will be visible to
readers as "Could not enable".

**`push_subscription_claim_refused` deserves a look every time.** An endpoint and its keys are minted
together by the browser and rotate together, so a request naming an existing endpoint *without* its
secret is not something a real browser produces. One or two are most likely a client bug; a pattern
means endpoints are leaking somewhere they can be replayed from — check what is being logged, shared
in support tickets, or captured in HAR files.

There are no metrics yet — counting log lines is the whole facility.

### What the ownership check does and does not do

Reassigning an endpoint between accounts is legitimate and supported: one browser, a reader signs
out, another signs in. Since it is the same browser it holds the same subscription, so the request
carries the same `auth` secret and the handover succeeds.

What the check refuses is a request that names an endpoint it does not hold the secret for. That
raises the bar from **"knows a URL"** — which leaks through logs, screenshots and HAR files — to
**"holds the subscription material"**, which requires the browser itself.

It is **not** proof of possession. Web Push offers none without sending a challenge to the device and
having it answer, and nothing sends anything in B1. An attacker who has both an endpoint and its
`auth` secret has, by definition, the browser's subscription — and the check cannot tell them from
the reader.

---

## 7. Delivery

### What one cycle does

The worker hangs off the ingestion poller's post-cycle seam, immediately after breaking-story
detection, so an event recorded on a cycle is delivered on that same cycle. It **starts a thread and
returns** — the poller's job is keeping the corpus fresh, and blocking it on a third party's network
would trade a delayed notification for a stale corpus, the worse of the two.

One run at a time. A request arriving while a run is in flight is **dropped, not queued**: a slow push
service would otherwise turn every poll cycle into another overlapping fan-out. Nothing is lost, since
the next cycle re-derives the same work.

Then, per cycle:

1. **Categories with live events** in the last 24 hours. B2 delivers what `notification_events`
   produced — the event-driven kinds. A reader's weekly report is *not* pushed, which is why enabling
   delivery does not immediately fire every cadence notification the registry would evaluate.
2. **Candidate devices**, from the denormalised per-category flags on `push_subscriptions`. That index
   is an accelerator, never the authority: consent is decided per reader against real settings, so a
   stale flag can cost a candidate but can never produce an unconsented send.
3. **Per reader**, the Phase A decision path with `channel="push"` — the same evaluation the inbox
   uses, reading `notifications.categories.<c>.push` instead of `.inApp`. The daily cap applies.
4. **Plan, then send.** Planning reads the database on the worker's own thread; sending happens on a
   pool of four, in waves, with the 120-second run deadline checked between waves. A run also
   plans at most 1000 sends (`MAX_JOBS_PER_RUN`) — the cap is the mechanism and the deadline is
   the backstop. Work not planned is not claimed, so the next cycle takes it with nothing to
   unwind.
5. **Claim, send, record.** Every attempt takes a row in `notification_deliveries` first
   (`UNIQUE(notification_id, channel, subscription_id)`), which is what stops each cycle re-sending
   every unexpired notification to every device.

### The delivery ledger

```bash
docker exec -i deploy-api-1 python - <<'PY'
import sys; sys.path.insert(0, '/app/examples')
import store, sqlalchemy as sa
with store.Store().session() as s:
    print("--- last 20 attempts")
    for row in s.execute(sa.text(
        "select id, notification_id, subscription_id, status, status_code, detail, attempted_at "
        "from notification_deliveries order by id desc limit 20")).all():
        print(row)
    print("--- outcome mix, last 24h")
    for row in s.execute(sa.text(
        "select status, count(*) from notification_deliveries "
        "where attempted_at > datetime('now','-1 day') group by status order by 2 desc")).all():
        print(row)
PY
```

`subscription_id` is deliberately **not** a foreign key: pruning a dead device must not erase the
record that we tried to reach it.

### Reading the outcome mix

This is the table that makes the platform operable, and the reason a failed send is never recorded as
just "failed" — a rising `expired` rate and a rising `permanent` rate need opposite responses.

| Status | Codes | What it means | What to do |
|---|---|---|---|
| `success` | 2xx | The push service accepted it. Not proof a human saw it — no such signal exists. | Nothing. |
| `expired` | 404, 410 | The subscription is gone forever. The row is deleted immediately. | Nothing. Ordinary attrition: reinstalls, cleared site data, browsers pruning idle subscriptions. A *spike* usually follows a key rotation (§5). |
| `transient` | 429, 5xx | The service is unwell; the subscription is fine. | Nothing to fix on our side — it is **retried** (§7.1). A `429` carrying `Retry-After` is honoured. Sustained 429 means we are being rate-limited faster than the backoff spreads us out. |
| `timeout` | — | No answer inside `RWE_PUSH_SEND_TIMEOUT_MS`. Delivered or not — unknowable. | **Retried.** Check the deadline is not too tight before assuming the service is slow. |
| `permanent` | 400, 401, 403, 413, anything unrecognised | **Our request is wrong** — malformed payload, VAPID mismatch, payload over the size limit. | Investigate every time. This is a bug we shipped, not weather. A 401/403 across all devices is a mismatched key pair (§2). |

An unrecognised status is classified `permanent`, not `transient`, on purpose: once retries exist,
treating an unknown answer as retryable is how a sender starts hammering a service that is saying no.

### 7.1 The retry ladder

A `transient` or `timeout` outcome schedules another attempt. `expired`, `permanent` and `success` do
not: the first two cannot succeed on a repeat, and repeating them just spends requests.

**The schedule is a column, not a timer.** `notification_deliveries.next_attempt_at` is the entire
scheduler — there is no queue, nothing in memory, and nothing to lose when the container restarts
mid-fan-out. A run finds due work by querying for it. This is also why a deploy during a backlog costs
nothing: the next run picks up exactly where the last one stopped.

**Three independent bounds**, each answering a different question. All three live in
`examples/push_retry.py` and are constants rather than environment variables — they are contract, not
tuning, and changing one without checking the other two breaks the ladder silently:

| Bound | Value | The question it answers |
|---|---|---|
| `MAX_ATTEMPTS` | 5 | *How many times.* Without it, a permanently-unreachable service turns one notification into an unbounded stream of requests. |
| `BASE_SECONDS` / `MAX_BACKOFF_SECONDS` | 30 s → 15 min | *How far apart.* Doubling per attempt, with **equal jitter** (half fixed, half random) so a fan-out that failed together does not retry together. The cap stops the exponent scheduling an attempt days out. |
| `MAX_DELIVERY_AGE_SECONDS` | 4 h | *For how long overall* — and this is the one that matters. A notification that arrives late enough is not a late notification, it is a **wrong** one: "breaking news" four hours after the fact describes something that has stopped being true, and the reader cannot tell from a lock screen. Deliberately equal to the transport's `ttl`, because the push service drops the message at the same moment anyway. |

With the defaults the whole ladder spans roughly half an hour, well inside the age bound. A test
asserts that relationship holds, so retuning one bound cannot quietly invalidate another.

**`Retry-After` is a floor, never a ceiling.** A push service asking for *more* time gets it — asking
again sooner is the definition of hammering. One asking for less does not get to shorten our backoff.
A value long enough to push the attempt past the age bound ends the ladder rather than parking it.

**A delivery is abandoned without sending** when, at the moment it comes due, the device has been
unregistered or pruned, the reader has withdrawn consent for that kind on the push channel, the
notification no longer exists, or the age bound has passed. All four are ordinary. Consent is
re-checked on **every** attempt, not only the first: it is the reader's decision and they may have
changed it since.

**Restart recovery.** A row left `pending` — claimed, never resolved — is what a container restart
mid-send leaves behind. After `LEASE_SECONDS` (15 minutes, far longer than any send can take) another
run takes it over and logs `push_delivery_recovered`. B2 abandoned these silently; the lease is what
makes recovering them safe, and the Notification `tag` collapses a duplicate at the OS level if the
first attempt did in fact land.

```sql
-- Deliveries currently on the ladder, and when each comes due
SELECT id, notification_id, subscription_id, status, attempts, next_attempt_at
  FROM notification_deliveries WHERE next_attempt_at IS NOT NULL ORDER BY next_attempt_at;

-- Gave up: retryable, out of budget. `attempts` at the cap means the service never came back.
SELECT status, attempts, count(*) FROM notification_deliveries
 WHERE next_attempt_at IS NULL AND status IN ('transient','timeout') GROUP BY 1, 2;

-- Claimed and never resolved. A handful after a deploy is expected; a growing number is not.
SELECT count(*) FROM notification_deliveries WHERE status = 'pending';
```

### 7.2 Rate limiting

A **token bucket per push service**, not a global one. Endpoints belong to a handful of independent
operators — `fcm.googleapis.com`, `updates.push.services.mozilla.com`, `*.notify.windows.com` — and a
global limit throttles Firefox because Chrome is slow, which punishes the wrong readers for someone
else's bad day.

A bucket rather than a fixed minimum gap, because fan-outs are bursty by nature: an event produces
every send it will ever produce inside one cycle, then nothing for an hour. Idle time pays for the
burst, which is both faster and closer to what a published rate limit actually means.

**On by default at 10/s per service.** The failure it prevents is silent and gradual: trip a push
service's own limit and every send comes back `429`, which the retry ladder then dutifully repeats —
so the symptom is a slow, failing pipeline with no single thing to point at. Throttling delays sends;
it never drops them.

```bash
$EDITOR deploy/.env         # RWE_PUSH_MAX_SENDS_PER_SECOND=25   (or 0 to switch it off)
deploy/ops/restart.sh api
$COMPOSE logs api | grep push_rate_limited     # are we throttling ourselves, and for which host?
```

This is the **proactive** half. `Retry-After` (§7.1) is the reactive half — the service telling us it
has had enough. They are complementary: one is about not arriving at that point, the other about
behaving once you have.

### 7.3 Metrics

Served by the existing internal-only `/api/metrics` — the same snapshot as everything else, because an
incident should have one place to look, not two. Every push series is prefixed `push_`.

```bash
# Inside the container, and with the internal secret from the container's own env — /api/metrics is
# internal-only and answers 404 without it. Port 8000 is not published on the AWS stack (see §3).
docker exec deploy-api-1 python -c "
import urllib.request, os, json
req = urllib.request.Request('http://127.0.0.1:8000/api/metrics',
                             headers={'X-IH-Auth': os.environ.get('RWE_INTERNAL_SECRET','')})
counters = json.load(urllib.request.urlopen(req))['counters']
print(json.dumps({k: v for k, v in counters.items() if k.startswith('push_')}, indent=2))"
```

| Series | What it is |
|---|---|
| `push_runs_total`, `push_run_ms` | Fan-outs, and how long each took. The duration is the number that says whether the pipeline is keeping up: a run longer than the poll interval means every cycle starts behind, and the dropped-not-queued rule turns that into *fewer* fan-outs, not more. |
| `push_considered_total`, `push_attempted_total` | Planned, and actually sent. A gap means the deadline or the job budget cut a run short. |
| `push_succeeded_total`, `push_failed_total` | The headline split. |
| `push_failed_expired_total` / `_timeout_total` / `_transient_total` / `_permanent_total` | **The reason this section exists.** A rising `expired` rate is ordinary attrition; a rising `permanent` rate is a credential defect we shipped. One "failures" number cannot tell them apart, and they need opposite responses. |
| `push_pruned_total` | Devices removed on 404/410. |
| `push_retries_scheduled_total` / `_exhausted_total` / `_abandoned_total` | The ladder's shape. `scheduled` rising with `exhausted` flat is a service having a bad minute; `exhausted` rising is one that never came back. |
| `push_deliveries_recovered_total` | Rows taken over after a process died mid-send. Non-zero after a deploy is expected. |
| `push_rate_limited_total`, `push_rate_limit_wait_ms` | How often *we* are the reason a send waited. A fan-out slow because we are throttling ourselves looks exactly like one slow because the push service is — and the fixes are opposite. |

Every counter is registered at zero on startup. A missing series and a zero series look identical at
3am and mean opposite things, and nobody can alert on a metric that does not exist until it fires.

### 7.4 Startup and shutdown

**On startup** the engine registers the metric series and logs `push_startup_backlog` if the previous
process left anything behind — `pending` (claimed, never resolved: a process died mid-send) and
`scheduled` (the retry ladder's depth). The lease recovers `pending` rows fifteen minutes later
anyway; the report exists so "notifications were late after that deploy" is a number visible at the
moment it is caused rather than a mystery afterwards.

**On shutdown** the worker stops between waves of four sends. A request already handed to a push
service is allowed to finish and be recorded — abandoning it would mean an outcome the ledger never
learns, which is worse than one more send. Anything not yet started is simply not started.

Graceful, not guaranteed: the wait is capped at five seconds, because a container being stopped has
its own clock (Docker sends SIGKILL ten seconds after SIGTERM by default) and a shutdown that outruns
it is not graceful, only late. `push_shutdown_incomplete` says it happened. **Nothing is lost either
way** — what is left behind is an unresolved claim, which is exactly what the lease recovers, so the
worst case of an ungraceful stop is a delivery that is late.

### Delivery log events

| Event | Level | Fields | What it tells you |
|---|---|---|---|
| `push_send_started` | INFO | `notificationId`, `subscriptionId`, `kind`, `attempt`, `bytes` | A claim was taken and a payload built. Every one of these should be followed by exactly one outcome line. `attempt` is 1 on a first try. |
| `push_send_succeeded` | INFO | + `attempt` | The push service accepted it. |
| `push_send_failed` | **WARNING** | + `attempt`, `status`, `statusCode`, `detail` | Anything that is not success or timeout. `status` is the classification above — read it, not just the code. |
| `push_send_timeout` | **WARNING** | `notificationId`, `subscriptionId`, `attempt` | No answer inside the deadline. |
| `push_send_error` | **WARNING** | + `error` (exception **type**, never a body) | The send path itself raised. A bug, not a push-service answer. |
| `push_subscription_pruned` | INFO | `subscriptionId`, `userId`, `statusCode` | A 404/410 device was deleted immediately. |
| `push_payload_oversize` | **WARNING** | `notificationId`, `bytes` | A payload over the 1 KB budget. Logged and still attempted — a payload this big is a defect upstream, and silently mangling it would hide the defect. |
| `push_retry_scheduled` | INFO | `notificationId`, `subscriptionId`, `attempt`, `nextAttemptAt`, `retryAfter` | A retryable failure earned another attempt. `retryAfter` is the raw header when the service sent one. |
| `push_retry_exhausted` | **WARNING** | + `attempts`, `status`, `reason` | The ladder gave up. `reason` is `attempts` (the service never came back) or `age` (the delivery outlived its usefulness) — different problems with different fixes. |
| `push_retry_abandoned` | INFO | `deliveryId`, `subscriptionId`, `attempts`, `reason` | A due delivery was closed without sending. `reason` ∈ `age`, `subscription_gone`, `consent_withdrawn`, `notification_gone`. All ordinary. |
| `push_delivery_recovered` | **WARNING** | `deliveryId`, `subscriptionId`, `attempts` | A row left claimed by a process that died was taken over after the lease. A few after a deploy is expected. |
| `push_retry_scan_failed` | **WARNING** | `error` | The ledger could not be read. The fresh fan-out still ran. |
| `push_retry_plan_failed` | **WARNING** | `deliveryId`, `error` | One due row could not be planned. The rest of the scan continued. |
| `push_record_failed` | **WARNING** | `deliveryId`, `error` | A send happened but its result could not be written. The lease will re-attempt it — an over-delivery risk that the notification `tag` collapses. |
| `push_run_complete` | INFO | `considered`, `sent`, `failed`, `pruned`, `skipped`, `retried`, `scheduled`, `exhausted`, `recovered`, `abandoned` | One fan-out finished. `skipped` counts claims an earlier cycle already took (or that the ladder still owns), and a steady non-zero value there is normal. |
| `push_run_deadline` | **WARNING** | `plannedReaders`, or `phase`/`sent`/`unsent` | A run hit the 120-second bound. In the `send` phase the unsent jobs keep their claims and the lease recovers them. Repeated appearances mean the fan-out has outgrown one cycle. |
| `push_run_budget_spent` | **WARNING** | `phase`, `planned` | A run reached `MAX_JOBS_PER_RUN`. Nothing was claimed beyond it, so the next cycle continues cleanly. Seeing this every cycle means the subscriber base has outgrown the cap. |
| `push_reader_failed` | **WARNING** | `userId`, `error` | One reader's evaluation raised. The fan-out continued without them. |
| `push_run_failed` | **WARNING** | `error` | The background run itself died. Should never appear. |
| `push_delivery_request_failed` | **WARNING** | `error` | The poller could not even start a run. Should never appear. |
| `push_rate_limited` | INFO | `subscriptionId`, `host`, `waitedMs` | A send waited on **our** limit, not the service's (§7.2). |
| `push_run_stopped` | INFO | `sent`, `unsent` | A run stopped between waves because the process is shutting down. The unsent jobs keep their claims and the lease recovers them. |
| `push_startup_backlog` | **WARNING** | `pending`, `scheduled`, `due` | What the previous process left behind (§7.4). |
| `push_startup_scan_failed` | **WARNING** | `error` | The backlog could not be counted. The engine still came up — a report is never worth failing startup. |
| `push_shutdown_incomplete` | **WARNING** | `timeoutSeconds` | A run outlived the shutdown grace period. Not an error; the lease recovers its claims. |

As with registration: **no endpoints and no keys.** A delivery line carries ids, a status and a code.

```bash
$COMPOSE logs api | grep -o '"event":"push_run_complete".*' | tail -20   # did it run, and do what?
$COMPOSE logs api | grep -o '"status":"[a-z]*"' | sort | uniq -c        # the outcome mix
$COMPOSE logs api | grep push_send_failed | grep -v '"status":"transient"'   # the ones that are ours
$COMPOSE logs api | grep push_payload_oversize                          # upstream defects
```

### What is and is not verified

The call into `pywebpush` **is** covered — `tests/test_push_transport_live.py` drives the real
transport against a local HTTP server with a real VAPID pair (generated by §2's own recipe, so the
documentation is under test too) and a real P-256 subscription keypair. It asserts the request is
signed (`Authorization: vapid …` carrying the public key), encrypted (`Content-Encoding: aes128gcm`,
and the headline is provably not on the wire in plaintext), carries the 4-hour TTL the retry ladder's
age bound is pinned to, and decrypts back to exactly the payload that was built. It also drives 201 /
410 / 429 and connection-refused through the real socket, confirming the classifications match what
the injected-transport tests assert.

That test **skips** where `pywebpush` is not installed — it lives in the `serve` extra, so a bare
engine checkout still runs everything else. It executes in the API image, which installs `.[serve]`.

**What no test covers is a real push service's own behaviour**: its rate limits, its interpretation of
TTL, and when it decides a subscription is gone. Nothing short of sending to Google or Mozilla covers
that. So the first production send is still the first test of *that*, and the check remains: turn
delivery on with a device of your own registered (§3) and look for `push_send_succeeded`, because a
quiet log is equally consistent with the worker never having run.

---

## 8. Troubleshooting

| Symptom | Check | Cause / fix |
|---|---|---|
| No control in Settings | `curl http://127.0.0.1:8000/api/push/config` | `enabled:false` **and this device is not registered** ⇒ correct (§1); a still-registered device shows the paused row instead. `enabled:true` but no control ⇒ the browser lacks the APIs (Safari before 16.4, or a non-HTTPS origin). |
| Control appears, toggle does nothing | browser console | The permission prompt was dismissed. Dismissal is neither granted nor denied; the toggle stays off and can be tried again. |
| Toggle shows "blocked" copy | browser site settings | The reader denied notifications. `requestPermission()` is a permanent no-op after that — only the reader can undo it, in browser settings. This is why the control is disabled rather than retried. |
| Toggle fails with "Could not enable" | `push_subscription_rejected` in the log (§6) | A `422` means the browser produced a subscription the engine rejected (endpoint not https, keys not base64url). A `503` means push was switched off between the page load and the click. |
| Subscription rows accumulate for one reader | the query in §3 | Bounded by `RWE_PUSH_MAX_DEVICES` (10). A recent key rotation legitimately replaces one row per returning device (§5). Many rows for one device *without* a rotation means the endpoint is changing every visit, which is a browser-side anomaly worth reporting. |
| A reader says push stopped working on an old device | `push_subscription_evicted` (§6) | The device cap dropped it. Expected if they use more than `RWE_PUSH_MAX_DEVICES` browsers; re-enabling on that device restores it, and raising the cap prevents a recurrence. |
| A registration returns `409` | `push_subscription_claim_refused` (§6) | The endpoint is already registered to a different account and the request did not carry its secret. Legitimate on no real browser — see §6. |
| Rows exist but nothing arrives | the `can send` check in §3 | Almost always delivery not switched on, or on with a VAPID variable missing — which is silent by design. If it reports `can send: True`, look for `push_run_complete` before looking anywhere else. |
| `push_run_complete` shows `considered: 0` | events, then consent | No live event in the last 24h (nothing to send — the common case), or no reader has `notifications.categories.breaking.push` set. The `sent` count only ever counts consenting devices. |
| Notification arrives saying "This site has been updated in the background" | `/sw-data.js` in the browser's Network tab | The service worker received a push and could not render it. That message is the browser's, not ours, and it means the render path threw — most likely a missing or stale `sw-data.js` (a build artifact, generated by `npm run build`). |
| Notification arrives in the wrong language | — | The device's stored language wins over the payload's, deliberately (§4 of the architecture): a push can sit under its TTL for hours, so the language captured at send time can be stale. The reader changing language in the app fixes it on the next render. |
| Tapping a notification opens the wrong page | — | For a *known* kind the worker derives the destination itself and ignores the payload's `href`. A wrong page means the metadata table and the engine disagree — `tests/test_push_delivery.py` cross-checks them, so this should fail CI before it reaches a reader. |
| Every send is `permanent` with 401/403 | §2 | A mismatched VAPID pair — a public key from one generation with a private key from another. Regenerate both together. |
| A burst of `expired` right after a rotation | §5 | Expected. Every subscription is bound to the key it was created against; devices heal on their readers' next visit. |
| A reader says push stopped after they re-enabled it | — | Fixed in the subscription-lifecycle commit. SQLite reuses row ids, so a device recreated after a `410` prune used to inherit the pruned one's ledger rows and be skipped for anything the old device received. A fresh registration now discards them. |
| A device goes quiet with the toggle still reading "on" | `push_subscription_created` with `reason=repair` | Two ways the browser and the engine desynchronise, both self-healing on the reader's next page load — **any** page, since reconciliation moved into the app shell: the engine pruned the row on a `410` while the browser kept its subscription, or the browser rotated the subscription without telling the page. The repair re-registers and the log line says which. |
| The browser holds a subscription but `push_subscriptions` has no row, and reloading does not fix it | `push_repair_*` in the **browser** console (§6) | Reconciliation ran and gave up, and the engine log cannot show you why — nothing reached it. `push_repair_failed` names the step. Before this was logged, this state was indistinguishable from "nothing to repair" and could only be diagnosed by replaying the sequence by hand. |
| No `push_repair_*` lines at all, on a device that should need one | the page is under `/(app)` and signed in | The reconciler mounts in the authenticated shell only — a user-scoped request would be a 401 for an anonymous visitor. The landing, sign-in and onboarding pages do not reconcile. |
| The same notification arrives twice on one device | `push_record_failed`, `push_delivery_recovered` (§7) | Delivery is at-least-once; the Notification `tag` normally collapses a repeat at the OS level. Two visible copies means the two payloads carried different `dedupeKey`s, which is a bug worth reporting. |
| A delivery sits `pending` for a long time | the `pending` query in §7.1 | A process died mid-send. It is recovered after the 15-minute lease. A *growing* count means runs are dying, not just one. |
| `push_retry_exhausted` with `reason: age` | §7.1 | The delivery outlived its usefulness rather than running out of attempts. Expected after a long push-service outage; the notification was correctly not sent. |

**The device cap's eviction is not `410` pruning.** The cap bounds how many devices one reader holds;
pruning removes endpoints a push service has declared gone. Both delete rows, for unrelated reasons.

---

## 9. What B4 does not include

Stated explicitly so the absence is not read as a defect.

**No exactly-once delivery, and there cannot be.** Web Push offers no acknowledgement that a human
saw anything; a `success` means the push *service* accepted the message. The ladder is therefore
at-least-once with three collapsing mechanisms behind it — the delivery ledger's UNIQUE constraint,
the lease, and the Notification `tag`. The residual risk is narrow and named: a send that succeeded but
whose result could not be written (`push_record_failed`) is re-attempted after the lease, and the
device shows one notification because the `tag` collapses it.

**No batching.** Each send is its own request. Web Push has no batch endpoint that all services
implement, so this is the protocol's shape rather than a shortcut.

**No metrics export.** The counters live in process and are read via `/api/metrics` (§7.3). A restart
resets them, and two replicas would each hold their own — neither matters for a single-container
deployment, and both are why §7.3's numbers are for reading during an incident rather than for
alerting on trends. Draining the snapshot into Prometheus is a later phase and touches no call site.

**No adaptive rate limiting.** The per-service limit is a fixed configured rate. It does not learn
from `429`s: sustained throttling is visible (§7.3) and the response is an operator lowering the
number, not the pipeline lowering it itself.

**No cross-process coordination.** The one-run-at-a-time rule, the rate limiter's buckets, and the
shutdown flag are all in-process. A second replica would fan out concurrently with the first — safe,
because the delivery ledger's UNIQUE constraint and the lease are in the database where both can see
them, but the rate limit would effectively double.

**No retry tuning by configuration.** The three bounds are constants in `examples/push_retry.py`. They
interact — the ladder has to fit inside the age bound — so exposing them individually as environment
variables would let an operator produce a ladder whose later attempts can never happen.

**No new notification kinds.** Only the event-driven ones fan out to push; the six cadence and
state-alert kinds that predate channels remain in-app.

---

*Related: `docs/BROWSER_PUSH_ARCHITECTURE.md` (the frozen design), `docs/NOTIFICATION_PLATFORM.md`
(Phase A, which decides what is worth notifying), `docs/DEPLOYMENT_RUNBOOK.md`,
`docs/PRODUCTION_ENVIRONMENT.md`.*
