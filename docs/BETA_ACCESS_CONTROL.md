# Beta Access Control (BA1) — invite-only allowlist

Keeps the application **private during the closed beta**: only approved email addresses can
authenticate. This is a **launch operation**, not a product feature — it gates sign-in only and changes
nothing in the recommendation engine, analytics (PA1), observability (OBS1), or business logic.

## How it works

- Enforcement is in the NextAuth **`signIn` callback** (`web/lib/auth.ts`), the authentication boundary.
  A non-approved Google sign-in is rejected **before** a session or an engine account is created — a
  denied user never gets in and never appears as a user anywhere.
- The decision logic is the pure, tested `web/lib/beta-access.ts` (`isEmailAllowed`).
- A denied user is redirected to `/signin?error=AccessDenied`, which shows a **friendly, localized
  invite-only message** (`signin.denied.*`, all 5 languages).
- Each denial is logged as a structured line for the operator — `{"event":"beta_access_denied","email":
  …,"reason":…}` — so you can see who tried and decide whether to approve them (OBS1-style; **no PA1
  analytics event is added**).
- The **dev demo login** (non-production only) is **not** gated, so local development stays zero-config.

**PA1 analytics are preserved for approved users** (their flow is unchanged). A denied visitor's
pre-auth `signin_started` still fires as an anonymous event, but they never reach `login_success` — so
"denied attempts" are visible as anonymous `signin_started` without a following sign-in. **OBS1 is
untouched.**

## Configuration (all operator-editable, no code change)

Set on the **web tier** (the app that runs NextAuth):

| Variable | Meaning |
|---|---|
| `BETA_ALLOWLIST` | Approved entries: comma / newline / semicolon separated. Each is an **email** (`ada@example.com`) or a **domain** (`@example.com`, approves everyone at that domain). Case-insensitive; whitespace trimmed; `#` starts a comment. |
| `BETA_ALLOWLIST_FILE` | Optional path to a file in the same format, **appended** to `BETA_ALLOWLIST`. Lets you keep a long list in a mounted file and edit it without changing env. |
| `BETA_ACCESS_ENABLED` | `1`/`0` to force the gate on/off. **Defaults to ON in production** (`RWE_ENV=production`) and OFF in dev. |

**Fail-closed:** when the gate is **enabled but the allowlist is empty**, **everyone is denied** (a
misconfigured deploy stays private rather than accidentally opening) — and a warning is logged. So in
production you **must** populate `BETA_ALLOWLIST` (or the file) or nobody can sign in.

### Behavior matrix

| Gate | Allowlist | Email | Result |
|---|---|---|---|
| disabled (dev) | — | any | **allowed** (`disabled`) |
| enabled | empty | any | **denied** (`empty_allowlist`) — fail-closed |
| enabled | has entries | matches email or domain | **allowed** (`allowlisted`) |
| enabled | has entries | no match | **denied** (`not_allowlisted`) |
| enabled | has entries | missing | **denied** (`no_email`) |

## Adding / removing beta testers

### The one-liner (preferred)

`scripts/manage_users.py` edits `BETA_ALLOWLIST_FILE` — the file this gate re-reads on **every**
sign-in attempt — so onboarding a tester needs no restart, no redeploy, and no database edit:

```bash
python scripts/manage_users.py grant-access  alice@example.com
python scripts/manage_users.py revoke-access alice@example.com
python scripts/manage_users.py list-access
python scripts/manage_users.py check         alice@example.com   # exit 3 = the gate would deny
```

On the deployed host, run it inside the api container (which has Python and the same `/app/data`
mount the web tier reads):

```bash
cd /opt/ih
docker exec deploy-api-1 python scripts/manage_users.py grant-access alice@example.com
docker exec deploy-api-1 python scripts/manage_users.py list-access
```

`@example.com` grants a whole domain. Every command is idempotent — a repeat `grant-access` reports
"already granted" and changes nothing, which matters because re-running after a dropped SSH session
is the normal case and not an error.

**It does not create user rows, deliberately.** `Store.upsert_user_by_identity` keys on
`(provider, provider_account_id)`, not email, so a User row pre-created with only an address has no
Identity: the first real Google sign-in would not find it and would create a *second* user, leaving
the first as an orphan that silently splits that person's history. The account is created correctly
by the OAuth flow on first sign-in — granting access is exactly what lets that happen.

**Parity is enforced by tests.** The CLI re-implements this module's parser in Python, so
`tests/fixtures/beta_allowlist_parity.json` is read by both `tests/test_manage_users.py` and
`web/lib/beta-access.test.ts`. If the two implementations drift, a build fails instead of a tester
being told they have access they do not have.

`list-access` also cross-references the `users` table, so you can see who has been invited but has
not signed in yet. It degrades gracefully when the database is not reachable.

### Add a tester by hand
1. Append their email (or a whole domain) to `BETA_ALLOWLIST` **or** the `BETA_ALLOWLIST_FILE`:
   ```
   BETA_ALLOWLIST="ada@example.com, grace@example.com, @ourteam.dev"
   ```
2. Make the new value visible to the running web app:
   - **env var:** update the service env and **restart/redeploy** the web tier
     (`docker compose up -d web`, or your platform's restart).
   - **file (`BETA_ALLOWLIST_FILE`):** edit the file; it is re-read on each sign-in attempt, so **no
     restart is needed** — the new tester can sign in immediately. (Prefer the file for a beta you edit
     often.)
3. Send the invite; they sign in with **exactly** that Google email.

### Remove a tester
1. Delete their entry from `BETA_ALLOWLIST` / the file (restart if using the env var; the file is
   picked up on the next sign-in).
2. This blocks **future** sign-ins immediately. **Existing sessions:** sessions are stateless 30-day
   JWTs (not individually revocable), so a user who is already signed in keeps their session until it
   expires. To **force immediate re-authentication of everyone** (after which the removed user is
   denied), rotate `NEXTAUTH_SECRET` on the web tier and restart — this invalidates **all** sessions
   and every user simply signs in again. For a 5-user beta this is rarely needed; use it only for an
   urgent eviction.

## Verify locally (before relying on it in prod)

```bash
# turn the gate on in dev and allow one email:
cd web
BETA_ACCESS_ENABLED=1 BETA_ALLOWLIST="you@example.com" npm run build && \
BETA_ACCESS_ENABLED=1 BETA_ALLOWLIST="you@example.com" NODE_ENV=production npm start
#  → an allowed Google email signs in; any other email lands on /signin with the invite-only message.

# unit-test the logic directly:
npm test    # includes lib/beta-access.test.ts (parse, match, domain, fail-closed, file loading)
```

## What BA1 does NOT change

Recommendation engine · ranking · lifecycle · evaluation · report calculations · **PA1 analytics**
(preserved for approved users) · **OBS1 observability** (preserved; denials are logged) · authentication
providers (still Google-only in prod) · business logic. BA1 adds one allowlist check at the sign-in
boundary and a friendly denial screen — nothing else.


## Troubleshooting: "This beta is invite-only" after granting

The gate logs its reason on every denial. Read that first — it distinguishes every cause in one line:

```bash
docker logs deploy-web-1 2>&1 | grep beta_access_denied | tail -5
```

| `reason` | what it means | fix |
|---|---|---|
| `empty_allowlist` | the gate is ON and it read **no entries at all** | the file is unreadable from `web`, or `BETA_ALLOWLIST_FILE` is unset — see below |
| `not_allowlisted` | entries were read, this address is not among them | the address does not match their Google account exactly |
| `no_email` | Google returned no email | rare; check the OAuth scopes |

### `empty_allowlist` while the file clearly has entries

Check what the **web** container sees, not what the host has:

```bash
docker exec deploy-web-1 sh -c 'echo "$BETA_ALLOWLIST_FILE"; cat "$BETA_ALLOWLIST_FILE"'
```

Two ways this fails, and both look identical from the host:

1. **`BETA_ALLOWLIST_FILE` is unset in `deploy/.env`.** The gate never opens any file. Set it, then
   `docker compose … up -d web` once — env changes need a restart, file edits do not.
2. **`web` cannot see the path.** The CLI runs in `api`, where `/app/data` is mounted read-write;
   `web` had no volumes at all until the read-only `/app/data` mount was added. `loadAllowlist`
   catches the read error by design, so a missing mount produces silence rather than an error, and
   every grant is ignored. Fixed in `deploy/docker-compose.aws.yml`; a test asserts it.

**Setting the env var without mounting the path is worse than leaving both unset**, because it looks
configured. `list-access` reports what the CLI can see; the log line above reports what the gate
actually did.
