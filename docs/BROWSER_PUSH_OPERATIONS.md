# Browser Push — Deployment, VAPID keys, and Operations

Operational companion to [`BROWSER_PUSH_ARCHITECTURE.md`](BROWSER_PUSH_ARCHITECTURE.md), which is the
frozen design. This document covers what an operator does: generating keys, turning the feature on,
verifying it, rolling it back, and reading its failures.

**Current state: Phase B1 — registration only.** A browser can be asked for permission and can
register a push subscription; the engine stores it. **Nothing sends a push.** There is no worker, no
fan-out, no retry ladder. Enabling B1 in production changes exactly one thing a reader can see: a
"Push notifications on this device" control appears in Settings, and using it produces a browser
permission prompt. Readers who enable it will receive nothing until Phase B2 ships.

---

## 1. Configuration

All four variables live on the **`api`** service, in both compose files, and are read at call time —
so every change below is a `deploy/ops/restart.sh api`, never a rebuild.

| Variable | Default | Used in B1 | Meaning |
|---|---|---|---|
| `RWE_PUSH_ENABLED` | `0` (off) | yes | The switch. Off ⇒ every push route answers `503` and the web tier renders no control. |
| `RWE_VAPID_PUBLIC_KEY` | *(empty)* | yes | Served to browsers, which subscribe against it. Public by construction. |
| `RWE_VAPID_PRIVATE_KEY` | *(empty)* | **no** | Signs sends. Read by nothing until B2 — wired now so the pair is generated and stored once. |
| `RWE_VAPID_SUBJECT` | *(empty)* | **no** | The `mailto:` or `https:` contact given to push services. Also B2. |

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

Every push route immediately answers `503`, and the web tier stops rendering the control (its config
fetch reports the feature unavailable). Stored subscriptions are **kept**: they are devices readers
explicitly connected, and turning the feature back on should not ask everyone to opt in again.

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

## 6. Troubleshooting

| Symptom | Check | Cause / fix |
|---|---|---|
| No control in Settings | `curl http://127.0.0.1:8000/api/push/config` | `enabled:false` ⇒ switch off or key missing (§1). `enabled:true` but still no control ⇒ the browser lacks the APIs (Safari before 16.4, or a non-HTTPS origin). |
| Control appears, toggle does nothing | browser console | The permission prompt was dismissed. Dismissal is neither granted nor denied; the toggle stays off and can be tried again. |
| Toggle shows "blocked" copy | browser site settings | The reader denied notifications. `requestPermission()` is a permanent no-op after that — only the reader can undo it, in browser settings. This is why the control is disabled rather than retried. |
| Toggle fails with "Could not enable" | `docker logs deploy-api-1` | A `422` means the browser produced a subscription the engine rejected (endpoint not https, keys not base64url). A `503` means push was switched off between the page load and the click. |
| Subscription rows accumulate for one reader | the query in §3 | Expected up to one per device/browser. A recent key rotation legitimately replaces one row per returning device (§5). Many rows for one device *without* a rotation means the endpoint is changing every visit, which is a browser-side anomaly worth reporting. |
| Rows exist but nothing arrives | — | Correct in B1. Nothing sends yet. |

**What is NOT a symptom yet:** delivery failures, `410 Gone`, retry storms, fan-out latency. None of
that code exists. If something in this area misbehaves in B1, it is registration, not delivery.

---

## 7. What B1 does not include

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
