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

## The rest of the API — the Phase 2 audit

32 handlers outside `/api/me/` (plus NextAuth's own two, which are not candidates — see below). All
of them resolve identity through `engineAuthHeaders()`, which reads the session cookie only.

**A correction to the first draft of this document.** It listed `/api/discover`, `/api/stories`,
`/api/stories/[id]`, `/api/search`, `/api/publishers/[name]` and `/api/places/countries` as
"session-tuned" and therefore needing bearer support. They are not. Reading the engine handlers
rather than the web proxies settles it: `discover_feed`, `stories`, `story`, `search_feed`,
`publisher_profile` and `place_countries` take **no `Request` and no user id at all**. The web tier
sends `engineAuthHeaders()` and the engine ignores it. Six routes moved from "must generalise" to
"nothing to do", which is most of the difference between the guessed scope and the measured one.

### The classification

Three groups, decided by what the engine actually does with the identity:

| | meaning | count |
|---|---|---|
| **A — must support bearer** | the answer depends on which reader is asking | 15 handlers |
| **B — should remain session-only** | the credential a browser holds is the right one, or the shape is wrong for mobile | 4 handlers |
| **C — public / optional** | the answer is the same for everyone, or there is no identity involved | 13 handlers |

### A — must support bearer (15 handlers)

`Today` is what a bearer-authenticated mobile client gets **right now**, since it is treated as
anonymous. The distinction that matters is `401` versus wrong data: a `401` is a bug you find on the
first run, and demo data is a bug you ship.

| Route | Methods | Engine gate | Today, for a bearer client | Severity |
|---|---|---|---|---|
| `/api/dashboard` | GET | `_serve` + `_real_uid` | **the demo reader's dashboard** | silent wrong data |
| `/api/report` | GET | `_serve` + `_real_uid` | **the demo reader's report** | silent wrong data |
| `/api/recommendations` | GET | `_serve` + `_real_uid` | **the showcase feed** | silent wrong data |
| `/api/history` | GET | `_require_real_user` | `401` | visibly broken |
| `/api/analytics` | GET | `_require_real_user` | `401` | visibly broken |
| `/api/profile` | GET | `_require_real_user` | `401` | visibly broken |
| `/api/settings` | GET, POST | web-side gate | `401` | visibly broken |
| `/api/coach` | GET, POST | `_serve` + `_real_uid` | the sample greeting | wrong data |
| `/api/recommendations/explain` | GET | `_real_uid` | the anonymous explanation | wrong data |
| `/api/analyze` | POST | `_real_uid` | analysis runs but is not attributed | silent data loss |
| `/api/events` | POST | `_real_uid` | `user_id` recorded as `NULL` | silent data loss |
| `/api/stories/[id]/intelligence` | GET | `_real_uid` | `newSinceLastVisit` always empty | degraded |
| `/api/bootstrap` | GET | `X-IH-User-Id` | `{pushConfig}` only | degraded |

**`/api/recommendations` is the worst of the three silent ones, and not only because it is wrong.**
Every card carries a rationale — "this offers another political perspective" — which is a *claim
about the reader's existing diet*. `api_fastapi.py: _serve` says so in as many words, and the engine
already refuses to serve the sample feed to a signed-in reader who has read nothing for exactly that
reason. A mobile reader authenticated by a token is signed in; serving them the showcase feed would
bridge them away from a position they never held. That is a `docs/SIGNAL_INTEGRITY.md` violation, not
just a personalisation gap.

**`/api/settings` POST is a write with reach.** Political openness and Recommendation strength map to
per-request recommender hyperparameters (`engine.rec_params_from_settings`), so this endpoint changes
the feed. It is the one A-group route where getting the identity wrong corrupts stored state rather
than just returning the wrong body.

### B — should remain session-only (4 handlers)

| Route | Methods | Why |
|---|---|---|
| `/api/push/subscriptions` | GET, POST, DELETE | web-push/VAPID only |
| `/api/dev/diagnostics` | GET | dev-only; the engine returns `404` in production |

`/api/push/subscriptions` is the interesting one, and it is a deliberate **no**. It is not that
mobile does not need push — it is that the payload is a `PushSubscription` (endpoint URL, `p256dh`,
`auth`), which is a browser object. Native push is an APNs/FCM device token: a different credential,
a different lifecycle, a different revocation story. Generalising this route to accept a bearer token
would produce an endpoint that authenticates a mobile client and then cannot represent what it wants
to register. Native push needs its **own** registration endpoint, designed alongside the APNs/FCM
work — not this one widened.

### C — public or optional (13 handlers)

Nothing to do. A bearer client already gets the correct answer, or there is no per-reader answer.

| Route | Methods | Why nothing changes |
|---|---|---|
| `/api/discover` | GET | `discover_feed()` takes no request — pure catalog query |
| `/api/stories` | GET | `stories()` takes no request |
| `/api/stories/[id]` | GET | `story(story_id)` takes no request |
| `/api/search` | GET | `search_feed()` takes no request |
| `/api/publishers/[name]` | GET | `publisher_profile(name)` takes no request |
| `/api/places/countries` | GET | `place_countries()` takes no request |
| `/api/outlets` | GET | reference data; the web route sends no auth headers at all |
| `/api/push/config` | GET | the public VAPID key |
| `/api/estimate` | POST | stateless; outlets in, estimate out |
| `/api/topics` | GET | **returns `501`** — see below |
| `/api/client-errors` | POST | telemetry; attribution is a nice-to-have, not blocking |
| `/api/rum` | POST | telemetry; no auth on any path |
| `/api/unsubscribe` | POST | carries its own signed token and **must never accept a session or a bearer** — anything the caller could assert would be a way to unsubscribe somebody else |

**`/api/topics` is dead surface.** The route returns `501`; `services.topics()` and `useTopics()`
exist and nothing renders them. The Expo app should not port it. Worth deleting or implementing, but
that is neither Phase 1 nor Phase 2.

**`/api/auth/[...nextauth]` is not a candidate at all.** Mobile does not get a cookie session. It
signs in through the OAuth flow and exchanges the result for a token — that exchange is its own
design problem (today a token is minted from an already-signed-in browser at `/api/me/tokens`, which
is fine for the extension and is not a mobile sign-up flow).

## The minimum set before Phase 2 can begin

**8 handlers across 7 route files.** These are exactly the routes where the first Expo screen is
either broken or lying:

| # | Route | Methods | Shape it needs |
|---|---|---|---|
| 1 | `/api/dashboard` | GET | `optionalUser` |
| 2 | `/api/report` | GET | `optionalUser` |
| 3 | `/api/recommendations` | GET | `optionalUser` |
| 4 | `/api/history` | GET | `optionalUser` |
| 5 | `/api/analytics` | GET | `optionalUser` |
| 6 | `/api/profile` | GET | `optionalUser` |
| 7 | `/api/settings` | GET, POST | `requireUser` |

That is the six routes the mobile plan names, plus `/api/profile` — which belongs with them because
the profile screen is the one that shows the reader their own streak and saved count, and `401` there
is indistinguishable from a broken session.

**Six of the seven take `optionalUser`, not `requireUser`, and the reason is load-bearing.**
`/api/dashboard` and `/api/report` serve the demo reader to an anonymous caller *on purpose* — that
is the signed-out landing experience. `/api/history`, `/api/analytics` and `/api/profile` already
surface the engine's `401` for an anonymous caller and must go on doing so. `optionalUser` leaves the
anonymous path byte-identical in both cases and adds the bearer path beside it, which is what
"preserve current web behaviour" means here. Only `/api/settings` is `requireUser`, because it
already answers `401` from the web tier itself.

### Second tier — before a public launch, not before Phase 2

7 handlers: `/api/coach` (GET, POST), `/api/analyze`, `/api/events`,
`/api/recommendations/explain`, `/api/stories/[id]/intelligence`, `/api/bootstrap`. Each degrades
rather than breaks, and none of them blocks a first Expo build. `/api/events` should not be left long
— every mobile analytics event lands with `user_id = NULL` until it is done, and that data cannot be
reconstructed afterwards.

### What Phase 2 gets for free

`web/services/index.ts` is the react-query data layer: 33 call sites covering most of the API,
already typed against `types/domain.ts`. `web/services/api.ts` is the single axios instance behind
it — and its request interceptor is already a placeholder for exactly this:

```ts
api.interceptors.request.use((config) => {
  // e.g. const token = getToken(); if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});
```

So `packages/core` has a natural seam that already exists: move `services/` and `types/domain.ts`
across, give the interceptor a real token source on mobile and a no-op on web, and both clients call
the same functions.

**It is not the whole surface, though, and the gap is worth knowing before estimating.** Ten
endpoints are fetched with bare `fetch()` outside `services/`, in eight files:

| Caller | Endpoints |
|---|---|
| `components/shell-prefetch.tsx` | `/api/bootstrap` |
| `components/onboarding/onboarding-flow.tsx` | `/api/outlets`, `/api/estimate`, `/api/me/onboarding` |
| `app/signin/complete/page.tsx` | `/api/me`, `/api/me/onboarding` |
| `lib/continuation.ts` | `/api/me/continuation` |
| `lib/push-client.ts` | `/api/push/subscriptions` |
| `lib/observability.ts`, `components/rum-listener.tsx` | `/api/client-errors`, `/api/rum` |
| `app/unsubscribe/page.tsx` | `/api/unsubscribe` |

Plus `lib/record-read.ts`, which posts to `/api/me/reads` through `navigator.sendBeacon` — and
cannot move to axios at all, because the whole point is that the request survives the page
navigating away to the publisher.

None of these is hard, but each is a place where a mobile client would otherwise have no token
attached, and `sendBeacon` in particular has no React Native equivalent. Fold them into
`packages/core` as part of the move rather than discovering them one screen at a time.

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
