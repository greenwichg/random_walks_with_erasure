# Google OAuth Configuration — Wave 0 (hidden-view.com)

Google is the **only** sign-in method in production. This is the exact Google Cloud Console setup and the
two env values (`NEXTAUTH_URL`, `GOOGLE_CLIENT_ID/SECRET`) that must agree with it. Getting the redirect
URI or `NEXTAUTH_URL` wrong is the most common cause of a "redirect_uri_mismatch" or a login that loops —
so this doc is precise about the exact strings.

> DEPLOYMENT-ONLY: no application code changes. NextAuth + the Google provider are already implemented;
> BA1 (invite-only allowlist) runs *after* Google auth succeeds — see `docs/BETA_ACCESS_CONTROL.md`.

## The values that must match

| Where | Value (production) |
|---|---|
| Google → Authorized **JavaScript origin** | `https://hidden-view.com` |
| Google → Authorized **redirect URI** | `https://hidden-view.com/api/auth/callback/google` |
| `deploy/.env` → `NEXTAUTH_URL` | `https://hidden-view.com` |
| `deploy/.env` → `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | from the OAuth client below |

The redirect path `/api/auth/callback/google` is fixed by NextAuth (provider id `google`). `NEXTAUTH_URL`
is the **origin** of that URL and must be the exact `https://` apex — it also drives the `Secure` session
cookie. We serve the apex only and redirect `www→apex` (Caddy), so **www does not need its own OAuth
entry** (the redirect happens before the OAuth round-trip).

## Step by step (Google Cloud Console)

1. **Project** → create or select one (e.g. "Information Health – Beta").
2. **APIs & Services → OAuth consent screen**:
   - User type **External**. Publishing status can stay **Testing** for a closed beta (add the Wave-0
     Google accounts under **Test users** — Testing mode allows up to 100). App name, support email, and a
     logo are enough; you do not need Google verification for a small closed beta.
   - Scopes: the defaults (`openid`, `email`, `profile`) — no sensitive scopes.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type **Web application**.
   - **Authorized JavaScript origins** → add `https://hidden-view.com`.
   - **Authorized redirect URIs** → add `https://hidden-view.com/api/auth/callback/google`.
   - Create → copy the **Client ID** and **Client secret**.
4. Put them in `deploy/.env`:
   ```
   NEXTAUTH_URL=https://hidden-view.com
   GOOGLE_CLIENT_ID=<client id>
   GOOGLE_CLIENT_SECRET=<client secret>
   ```
   Then `deploy/ops/restart.sh web` (re-reads env).

> **Order of operations:** DNS + HTTPS must be live first (so `https://hidden-view.com` actually resolves
> and has a valid cert) — see `docs/ROUTE53_CONFIGURATION.md`. Then this OAuth config, then a real sign-in.

## Optional: staging / multiple origins
You may add extra origins + redirect URIs to the **same** OAuth client (e.g. a staging host) — Google
allows several. Each redirect URI must be the full `https://<host>/api/auth/callback/google`, and each
host's `NEXTAUTH_URL` must match its own origin. Do **not** add `http://` origins in production.

## Verify after deploy

1. **Allowed user:** open `https://hidden-view.com`, "Sign in with Google" with an **allowlisted** account
   → lands on the dashboard.
2. **Denied user (BA1):** sign in with a Google account **not** on `BETA_ALLOWLIST` → redirected to
   `/signin?error=AccessDenied` (friendly invite-only message); the engine log shows
   `{"event":"beta_access_denied",…}`.
3. `deploy/ops/preflight.sh` asserts `NEXTAUTH_URL` is https and the Google creds are set.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Error 400: redirect_uri_mismatch` | Redirect URI in Google ≠ `https://hidden-view.com/api/auth/callback/google` | Fix the URI (exact, https, no trailing slash); changes propagate in a few minutes |
| Login loops back to sign-in | `NEXTAUTH_URL` wrong (http, or wrong host) → cookie not `Secure`/domain mismatch | Set `NEXTAUTH_URL=https://hidden-view.com`, `restart.sh web` |
| "Access blocked: not verified" for a tester | Tester not in **Test users** while consent screen is in Testing | Add their Google email to Test users (or publish the app) |
| Allowed Google account still denied by the app | Email not in `BETA_ALLOWLIST` | Add it (`docs/BETA_ACCESS_CONTROL.md`), `restart.sh web` |
| Secret rotated, everyone signed out | `NEXTAUTH_SECRET` changed (invalidates all JWTs) | Expected; users simply sign in again |
