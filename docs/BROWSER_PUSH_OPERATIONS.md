# Browser Push — Deployment, VAPID keys, and Operations

Operational companion to [`BROWSER_PUSH_ARCHITECTURE.md`](BROWSER_PUSH_ARCHITECTURE.md), which is the
frozen design. This document covers what an operator does: generating keys, turning the feature on,
verifying it, rolling it back, and reading its failures.

**Current state: Phase B1 — registration only.** A browser can be asked for permission and can
register a push subscription; the engine stores it. **Nothing sends a push.** There is no worker, no
fan-out, no retry ladder. Enabling B1 in production changes exactly one thing a reader can see: a
"Push notifications on this device" control appears in Settings, and using it produces a browser
permission prompt. Readers who enable it will receive nothing until Phase B2 ships.

Every action on that surface is logged (§6), and a rollback closes registration without stranding the
readers who already registered (§4).

---

## 1. Configuration

All five variables live on the **`api`** service, in both compose files, and are read at call time —
so every change below is a `deploy/ops/restart.sh api`, never a rebuild.

| Variable | Default | Used in B1 | Meaning |
|---|---|---|---|
| `RWE_PUSH_ENABLED` | `0` (off) | yes | The switch. Off ⇒ **registration** answers `503`; reading and deleting a subscription stay open (§4). |
| `RWE_VAPID_PUBLIC_KEY` | *(empty)* | yes | Served to browsers, which subscribe against it. Public by construction. |
| `RWE_VAPID_PRIVATE_KEY` | *(empty)* | **no** | Signs sends. Read by nothing until B2 — wired now so the pair is generated and stored once. |
| `RWE_VAPID_SUBJECT` | *(empty)* | **no** | The `mailto:` or `https:` contact given to push services. Also B2. |
| `RWE_PUSH_MAX_DEVICES` | `10` | yes | How many devices one reader may hold. Past the cap the least-recently-registered is dropped. `0` = unbounded. |

`deploy/deployment-rules.json` enforces two things: the switch must stay wired on `api` whether or not
it is set (an OFF switch an operator cannot reach is the failure being guarded against), and turning it
on requires all three key variables — with the private key **interpolated from `deploy/.env`**, never
written into a compose file.

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
$EDITOR deploy/.env                       # the four variables above
deploy/ops/restart.sh api                 # read at call time — no rebuild
docker exec deploy-api-1 printenv | grep -E 'RWE_PUSH_ENABLED|RWE_VAPID'   # prove it landed
curl -s http://127.0.0.1:8000/api/push/config     # from the host; expect enabled:true
```

`printenv` showing nothing means the variable never reached the container — `environment:` is an
explicit allowlist and this stack has no `env_file:`, so a value in `deploy/.env` alone does not
suffice. That is the failure this repo has hit twice; the validator now guards it, but the check costs
nothing.

Then from a browser, signed in:

1. **Settings → Notifications** shows "Push notifications on this device". If it does not, the config
   endpoint is reporting the feature unavailable — go back to §1.
2. Toggling it on produces the browser's permission prompt. Granting it registers a subscription.
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

---

## 4. Rolling it back

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

Browsers keep their own subscription objects, which is harmless — nothing sends, so nothing arrives.

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

## 6. Logging

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

There are no metrics yet — counting log lines is the whole facility in B1.

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

## 7. Troubleshooting

| Symptom | Check | Cause / fix |
|---|---|---|
| No control in Settings | `curl http://127.0.0.1:8000/api/push/config` | `enabled:false` **and this device is not registered** ⇒ correct (§1); a still-registered device shows the paused row instead. `enabled:true` but no control ⇒ the browser lacks the APIs (Safari before 16.4, or a non-HTTPS origin). |
| Control appears, toggle does nothing | browser console | The permission prompt was dismissed. Dismissal is neither granted nor denied; the toggle stays off and can be tried again. |
| Toggle shows "blocked" copy | browser site settings | The reader denied notifications. `requestPermission()` is a permanent no-op after that — only the reader can undo it, in browser settings. This is why the control is disabled rather than retried. |
| Toggle fails with "Could not enable" | `push_subscription_rejected` in the log (§6) | A `422` means the browser produced a subscription the engine rejected (endpoint not https, keys not base64url). A `503` means push was switched off between the page load and the click. |
| Subscription rows accumulate for one reader | the query in §3 | Bounded by `RWE_PUSH_MAX_DEVICES` (10). A recent key rotation legitimately replaces one row per returning device (§5). Many rows for one device *without* a rotation means the endpoint is changing every visit, which is a browser-side anomaly worth reporting. |
| A reader says push stopped working on an old device | `push_subscription_evicted` (§6) | The device cap dropped it. Expected if they use more than `RWE_PUSH_MAX_DEVICES` browsers; re-enabling on that device restores it, and raising the cap prevents a recurrence. |
| A registration returns `409` | `push_subscription_claim_refused` (§6) | The endpoint is already registered to a different account and the request did not carry its secret. Legitimate on no real browser — see §6. |
| Rows exist but nothing arrives | — | Correct in B1. Nothing sends yet. |

**What is NOT a symptom yet:** delivery failures, `410 Gone`, retry storms, fan-out latency. None of
that code exists. Note that the device cap's eviction is *not* `410` pruning — it bounds how many
devices one reader holds; removing endpoints a push service has declared gone is Phase B2. If something in this area misbehaves in B1, it is registration, not delivery.

---

## 8. What B1 does not include

Stated explicitly so the absence is not read as a defect: no push is sent from anywhere; there is no
notification worker, no fan-out query, no retry or backoff, no `410` pruning, and no delivery metrics.
The service worker ships a **generic** render path only — architecture §6's floor, so that a device
holding a B1 subscription renders correctly rather than producing the browser's own "site has been
updated in the background" message when a later deploy first sends something. Metadata-driven
rendering is Phase B2.

---

*Related: `docs/BROWSER_PUSH_ARCHITECTURE.md` (the frozen design), `docs/NOTIFICATION_PLATFORM.md`
(Phase A, which decides what is worth notifying), `docs/DEPLOYMENT_RUNBOOK.md`,
`docs/PRODUCTION_ENVIRONMENT.md`.*
