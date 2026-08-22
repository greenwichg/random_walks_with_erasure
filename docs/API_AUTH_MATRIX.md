# The route/auth matrix

Every route handler under `web/app/api/`, and how it decides who is calling. Produced as the audit
for **Phase 1** of the mobile architecture work, and kept because "which routes accept which
credential" is the question a mobile client asks first and the codebase could not previously answer
without reading forty files.

The mobile architecture audit that called for it recommended Expo/React Native over a shared
TypeScript core, with the existing Next.js route handlers promoted to the single API for web,
Android and iOS. Generalising bearer authentication from one route to all of `/api/me/*` is the
first prerequisite of that plan, and the only part of it that ships to the existing web app.

## What Phase 1 changed, in one paragraph

Before: a per-user API token (`Authorization: Bearer …`) was accepted on **one** route,
`/api/me/reads`, where the session-then-token ladder was written inline. Every other route resolved
identity through `engineAuthHeaders()`, which reads the NextAuth session cookie and nothing else — so
a non-browser client was either refused or, worse, served the unattributed answer. After: the ladder
lives in `web/lib/auth-decision.ts` (the pure verdict) and `web/lib/require-user.ts` (the wiring), and
**all twelve** `/api/me/*` route files run it. No status code changed for any web caller.

## The three shapes

| | helper | anonymous caller | bearer token |
|---|---|---|---|
| **required** | `requireUser(request, msg)` | `401` — as before | accepted |
| **optional** | `optionalUser(request)` | the route's existing answer | accepted |
| **session-only** | `requireUser(request, msg, SESSION_ONLY)` | `401` — as before | `403` |

All three run the **same** decision, so the property that matters cannot depend on picking the right
one: **a bearer token that does not resolve is refused everywhere**, including on `optional` routes.
Falling through to the anonymous answer would serve demo content to a client holding a revoked
credential, and the client would have no way to tell it apart from a real answer.

`session-only` covers exactly `/api/me/tokens` and `/api/me/tokens/[id]`. A token that can mint
tokens outlives its own revocation; a token that can revoke can lock the owner out with the very
credential they are withdrawing; a token that can list enumerates the reader's other devices.

## `/api/me/*` — after Phase 1

`Anonymous` is the observed status for a caller presenting no credential, and it is unchanged from
before Phase 1 in every row.

| Route | Methods | Shape | Anonymous | Bearer |
|---|---|---|---|---|
| `/api/me` | GET | optional | `503` (engine refuses the unattributed call) | ✅ |
| `/api/me/continuation` | GET | optional | `200 null` | ✅ |
| `/api/me/geography` | GET | optional | `503` | ✅ |
| `/api/me/notifications` | GET | optional | `200 []` | ✅ |
| `/api/me/onboarding` | POST | optional | `503` | ✅ |
| `/api/me/notifications/[id]/seen` | POST | required | `401` | ✅ |
| `/api/me/reads` | POST | required | `401` | ✅ (was the only one) |
| `/api/me/recommendations/feedback` | GET, POST | required | `401` | ✅ |
| `/api/me/recommendations/opened` | POST | required | `401` | ✅ |
| `/api/me/saved` | GET, POST, DELETE | required | `401` | ✅ |
| `/api/me/tokens` | GET, POST | session-only | `401` | `403` |
| `/api/me/tokens/[id]` | DELETE | session-only | `401` | `403` |

16 handlers across 12 files. The count is pinned in two places — `lib/api-auth-guard.test.ts` counts
the tree, `e2e/specs/api-auth.spec.ts` counts the matrix — so neither can quietly shrink.

### Why five routes are `optional` rather than gated

`/api/me`, `/api/me/geography` and `/api/me/onboarding` answer `503` to an anonymous caller today,
because they forward an unattributed call and the engine's `401` collapses to `null` in
`backendGet`. A `401` would be more truthful. It is also a **different answer** than the one
`app/signin/complete/page.tsx` has read since it was written, and Phase 1 changes authentication, not
status codes. Correcting it is a separate, deliberate change.

`/api/me/notifications` (`200 []`) and `/api/me/continuation` (`200 null`) are anonymous-tolerant by
design — the header bell shows no badge, the continuation strip renders nothing. Both keep that for
a caller with no credential and refuse a caller whose credential failed.

## The rest of the API — not in Phase 1's scope

The other 32 handlers (plus NextAuth's own two, which are not candidates) still resolve identity
through `engineAuthHeaders()` — session-only. **They are not broken**; they are the next phase. A
bearer-authenticated client reaches them and is treated as anonymous, which for the personalised ones
means either a `401` or, worse, the demo reader's data in place of their own.

| Route | Methods | Today | Mobile needs it |
|---|---|---|---|
| `/api/analytics` | GET | session → personal; anon → engine `401` surfaced as `401` | yes |
| `/api/dashboard` | GET | session → personal; anon → demo | yes |
| `/api/discover` | GET | session-tuned ranking; anon → generic | yes |
| `/api/history` | GET | session → personal; anon → `401` | yes |
| `/api/profile` | GET | session → personal; anon → `401` | yes |
| `/api/recommendations` | GET | session → personal; anon → generic | yes |
| `/api/recommendations/explain` | GET | session-tuned | yes |
| `/api/report` | GET | session → personal; anon → demo | yes |
| `/api/settings` | GET, POST | gated: anon → `401` | yes |
| `/api/push/subscriptions` | GET, POST, DELETE | gated: anon → `401` | on APNs/FCM |
| `/api/coach` | GET, POST | session-tuned | yes |
| `/api/analyze` | POST | session-attributed | yes |
| `/api/estimate` | POST | stateless | no change |
| `/api/events` | POST | session-attributed analytics | yes |
| `/api/bootstrap` | GET | the shell aggregate; anon → `pushConfig` only | yes |
| `/api/search` | GET | session-tuned | yes |
| `/api/stories`, `/api/stories/[id]`, `/api/stories/[id]/intelligence` | GET | session-tuned | yes |
| `/api/publishers/[name]` | GET | session-tuned | yes |
| `/api/places/countries`, `/api/outlets`, `/api/topics` | GET | reference data | no change |
| `/api/push/config` | GET | public VAPID key | no change |
| `/api/client-errors`, `/api/rum` | POST | telemetry | no change |
| `/api/unsubscribe` | POST | its own signed token — never a session | no change |
| `/api/dev/diagnostics` | GET | dev only; `404` in production | no |
| `/api/auth/[...nextauth]` | — | NextAuth itself | see below |

`/api/settings` and `/api/push/subscriptions` are the two gated routes outside `/api/me/`. They are
the obvious next candidates: both already refuse anonymous callers, so migrating them is the
`required` shape with no behaviour question to answer.

**`/api/auth/[...nextauth]` is not a candidate.** Mobile does not get a cookie session; it signs in
through the OAuth flow and exchanges the result for a token. Designing that exchange is Phase 2 —
today a token is minted from an already-signed-in browser session at `/api/me/tokens`.

## The engine's side of the boundary

Unchanged by Phase 1, and worth restating because it is what makes any of this safe:

- The engine's `/api/me/*` surface trusts `X-IH-User-Id` **only** alongside `X-IH-Auth`
  (`RWE_INTERNAL_SECRET`). It is a private, server-to-server surface and can never be exposed.
- A bearer token never reaches the engine's public surface. The web tier is the only caller of
  `/api/internal/resolve-token`, which returns a user id; the read is then forwarded on the existing
  path with `engineHeadersForUserId`.
- Only the SHA-256 hash of a token is stored (`examples/store.py: ApiToken`). **Revocation is
  deletion of the row** — so a revoked token and one that never existed are indistinguishable to the
  resolver, which is why one `rejected` verdict covers revoked, unknown, and (should the model ever
  grow expiry) expired.

### Tokens have no expiry today

`ApiToken` carries `created_at` and `last_used_at`, and no expiry column. Nothing in the web tier
assumes otherwise: `resolveApiTokenResult` treats *any* refusal from the engine as `rejected`, so
adding server-side expiry later requires no change here. It is stated explicitly because
"rejects expired tokens" reads like a claim about a feature that exists, and it is not one.

## One deliberate behaviour change

`resolveApiToken` previously returned `null` for both "the engine says this token is invalid" and
"the engine did not answer", so `/api/me/reads` answered `401` in both cases. That was invisible to a
browser, which carries a cookie. It is not invisible to a mobile client: every deploy restarts the
engine, and a client that signs itself out on `401` would sign the reader out on every deploy.

`resolveApiTokenResult` (via the new `backendPostResult`) keeps the engine's status:

| Engine says | Verdict | Response |
|---|---|---|
| `401` / `403` | `rejected` | `401` |
| `2xx` with a numeric `userId` | `ok` | proceed |
| no answer (`status: 0`), `5xx`, or a `2xx` without an id | `unavailable` | `503` |

So one status changed anywhere in the app: `POST /api/me/reads` with a bearer token **while the
engine is unreachable** now answers `503` instead of `401`. No web path can reach it.

## What stops this from decaying

- `web/lib/api-auth-guard.test.ts` — runs in `npm test`. Scans `app/api/me/**` and fails when a route
  file omits the shared import, calls `engineAuthHeaders()` (or another session-only primitive)
  directly, exports a handler whose body never runs the check, or drops `SESSION_ONLY` from a token
  -management call. No allowlist: a rule that can be opted out of is guidance, not a guard.
- `web/lib/auth-decision.test.ts` — 19 unit tests over the pure verdict, including the cases no
  manual test reaches: revoked, cross-account, both-credentials, engine-down.
- `web/e2e/specs/api-auth.spec.ts` — the matrix above, against the real stack, with tokens minted and
  revoked through the engine. Also asserts attribution (the payload belongs to the token's owner,
  not the demo reader) and that a token cannot mint, list, or revoke tokens.
