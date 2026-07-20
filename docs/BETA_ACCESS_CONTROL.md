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

### Add a tester
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
