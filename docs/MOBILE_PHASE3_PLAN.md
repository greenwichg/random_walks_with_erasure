# Phase 3 audit — Expo shell, authentication, Recommendations

The audit before building. **No code has been written.**

## The headline: two of the eight scope items cannot be built on today's API

The brief says "bearer authentication is available across `/api/me/*`" — true, and Phase 1 delivered
it. But Recommendations does not live under `/api/me/*`, and neither do the settings that carry
Interest Intensity and the country preference. The Phase 2 audit
(`docs/API_AUTH_MATRIX.md`) identified eight handlers that must be generalised before an Expo app can
begin; **none of them has been implemented.** That work was audited and approved as a plan, not
executed.

This is not inference. Against the real stack, with a real token minted through the engine for a
reader who had **eight recorded reads**:

```
/api/recommendations
  bearer   200  [{"article":{"id":"S28","headline":"synthetic headline 28",…
  anon     200  [{"article":{"id":"S28","headline":"synthetic headline 28",…
  identical to anonymous? true

/api/settings
  bearer   401  {"error":{"code":"unauthorized","message":"Sign in to load or save settings."}}

/api/dashboard
  bearer   200  identical to anonymous (the demo reader's numbers)

POST /api/me/tokens with a bearer token -> 403
```

A valid, correctly-presented credential got **the showcase feed, byte for byte identical to an
anonymous request**. Not an error — an answer, and the wrong one. Every card in it carries "this
offers another political perspective", which is a claim about a reading diet that is not this
reader's. `api_fastapi.py: _serve` already refuses to serve that feed to a signed-in reader who has
read nothing, for exactly this reason; a token-authenticated reader is signed in and the engine
cannot currently tell.

**So scope items 6, 7 and 8 are blocked**, and building the screen first would mean demonstrating
Interest Intensity and Political Viewpoint Diversity against a reader who is not the one signed in.

## The second blocker: there is no way for a mobile app to get its first token

Scope item 4 says "exchange the Google identity for the existing Hidden View bearer-token flow."
**There is no such flow.** Today a token is minted at `POST /api/me/tokens` from an
already-signed-in browser session, which is right for the browser extension (the reader copies the
plaintext into it once) and is not a sign-up path.

That route is `SESSION_ONLY` **by deliberate Phase 1 design**, and the reason still holds: a token
that can mint tokens outlives its own revocation — revoke the stolen one and the one it minted still
works. Relaxing it to unblock mobile would undo a security decision to save building the right thing.

The right thing is a new server endpoint that takes a Google identity and returns a token. Every
piece it needs already exists:

| Step | Existing primitive |
|---|---|
| verify the Google ID token | new — `google-auth-library` or `jose` against Google's JWKS |
| enforce the closed beta | `isEmailAllowed()` — `web/lib/beta-access.ts`, the same gate `signInCallback` runs |
| map identity → engine user | `upsertEngineUser({ provider: "google", providerAccountId: sub, … })` |
| mint the token | engine `POST /api/me/tokens` with `engineHeadersForUserId(uid)` |

Nothing about the engine changes, and no secret leaves the server: the app sends an ID token, the
server verifies it against Google's public keys and returns a Hidden View token.

## What is already in place

**`mobile/` is a boundary and nothing else** — `package.json`, `tsconfig.json`, `README.md`, and three
empty directories (`app/`, `components/`, `design/`). No Expo config, no dependencies, no entry point.
`main` already points at `expo-router/entry`, which is a promise the scaffold has to keep.

**The shared logic the screen needs is already extracted, and the web page already imports it from
`@ih/core`.** `web/app/(app)/recommendations/page.tsx` reads:

```ts
import type { FeedbackAction, Recommendation } from "@ih/core/domain/types";
import { countryName }             from "@ih/core/logic/countries";
import { partitionByCountryMatch } from "@ih/core/logic/country-partition";
import { presentRecommendation }   from "@ih/core/logic/rec-presentation";
```

Those four modules plus `@ih/core/i18n/core` (`makeT`, `localizeExplanation`) and the five catalogs
are the entire logic surface of the mobile screen. Mobile imports the same four. **Nothing is
copied**, and there is no second implementation to keep in step.

`presentRecommendation` returns catalog *keys* and typed parameter refs (`claimKey`, `ctaKey`,
`PartRef`), never rendered strings — so the same function drives a `<span>` on web and a `<Text>` on
native, and the explanations are identical by construction rather than by review.

**The API client is already configured rather than environment-driven.** `configureApi({ baseUrl,
getToken })` landed in the Phase 2 split precisely for this, and `getToken` is unused on web because
the browser has a cookie.

## The four scope items that map onto shared code

| Scope item | Where it comes from | Status |
|---|---|---|
| Interest Intensity | `Settings.interests` (8 topics) → engine hyperparameters | needs `/api/settings` on bearer |
| Country preference | `Settings.recommendationCountry` + `Recommendation.countryMatch` → `partitionByCountryMatch` | needs `/api/settings` on bearer |
| Political Viewpoint Diversity | `@ih/core/logic/political` + `Settings.politicalOpenness` | shared; needs settings |
| Recommendation explanations | `presentRecommendation` + `localizeExplanation` + catalogs | fully shared, ready |

All four are **read** on mobile in this phase. None of them is an algorithm mobile implements — the
engine computes, `@ih/core` presents. `docs/CORE_MIGRATION_MAP.md` records why: the recommender,
the clustering and the Interest Intensity weighting are Python, and `packages/core` is the shared
*client* core.

One asset gap: `countryFlagSrc()` returns `/flags/xx.svg`, a web path served out of `web/public/`.
Mobile uses `countryFlag()` (the emoji) or ships its own asset set.

## What must be configured in the Google and Apple consoles

None of this can be done from here — it needs console access and, for Android, a signing key.

### Google Cloud Console — OAuth 2.0 client IDs

Google requires a **separate client ID per platform**. The existing `GOOGLE_CLIENT_ID` is a *Web*
client used by NextAuth and cannot be used by a native app.

| Client | Needs | Notes |
|---|---|---|
| **Web** (exists) | — | keep; NextAuth uses it |
| **iOS** (new) | the bundle identifier, e.g. `com.hiddenview.app` | no client secret; Google returns a reversed-client-ID URL scheme that goes in the Expo config |
| **Android** (new) | the package name **and the SHA-1 fingerprint of the signing certificate** | register **two**: the debug keystore for local development builds, and the EAS-managed release keystore (`eas credentials`) |

The Android SHA-1 is the step that most often stalls this work: sign-in fails with a bare
`DEVELOPER_ERROR` and no indication that a fingerprint is missing.

**OAuth consent screen.** While the app is in *Testing*, only explicitly listed test users can sign
in — capped at 100. That is a **second, independent gate** on top of Hidden View's own beta
allowlist (`BETA_ALLOWLIST`), and a tester will be refused if they are missing from either. Scopes
are `openid`, `email`, `profile` only — all non-sensitive, so no Google verification review is
needed.

**Server audience allowlist.** A native ID token's `aud` claim is the *native* client ID, not the web
one, so the exchange endpoint must accept a configured set: `GOOGLE_CLIENT_ID`,
`GOOGLE_IOS_CLIENT_ID`, `GOOGLE_ANDROID_CLIENT_ID`. An endpoint that checks only the web client ID
rejects every real mobile sign-in.

### Apple Developer

- A **bundle identifier** registered on the Apple Developer portal.
- The **iOS Simulator needs no membership**; a physical iPhone or TestFlight needs the Apple
  Developer Program ($99/yr) for provisioning. EAS can manage the profile.
- **No App Store submission** — explicitly out of scope, and nothing in this plan approaches it.

**Worth knowing now, because it shapes the auth design: App Store Review Guideline 4.8.** An app
that offers third-party sign-in (Google) must also offer **Sign in with Apple**. That does not apply
to development or TestFlight, so it does not block this phase — but the exchange endpoint should be
written to take a provider discriminator rather than hard-coding Google, so adding Apple later is an
argument rather than a second endpoint.

## Environment / toolchain findings

- Every package needed is published and reachable: `expo` 57, `expo-router` 57, `expo-auth-session`
  57, `expo-secure-store` 57, `react-native` 0.87.
- **This container cannot run the app.** No `adb`, no `xcrun`, no simulator, no `watchman`. So "real
  user authentication" and "real recommendation retrieval" on a device are things **you** run and I
  verify from the output — the same pattern as the SES work.
- **React version skew.** Web is on React 18.3; Expo SDK 57 ships React 19. npm workspaces will nest
  rather than hoist, which is correct, but Metro resolving the wrong React through a hoisted path is
  a classic monorepo failure. Metro needs `watchFolders: [repoRoot]` and explicit
  `nodeModulesPaths`, and this must be verified before anything else is debugged on top of it.

## The plan

Five stages. Each leaves the web app untouched and green.

### 3a — Server prerequisites (no mobile code)

1. **Generalise the Recommendations path to bearer.** `/api/recommendations` and
   `/api/recommendations/explain` take `optionalUser` (anonymous keeps the showcase feed — that is
   the signed-out landing experience); `/api/settings` GET+POST take `requireUser` (it already 401s).
   Extend `api-auth-guard.test.ts` to cover them, and extend `e2e/specs/api-auth.spec.ts`'s matrix.
2. **`POST /api/auth/mobile/exchange`.** Body `{ provider: "google", idToken }`. Verifies against
   Google's JWKS with an audience allowlist, runs `isEmailAllowed`, upserts the identity, mints a
   token, returns `{ token, expiresAt: null }`. Rate-limited; never echoes the ID token; logs a
   structured line per outcome the way `engine-identity.ts` does.
3. **Prove it before mobile exists**: an e2e spec that mints through the exchange endpoint with a
   stubbed verifier and reads a *personalised* feed with the result.

**Exit criterion, measurable:** the probe at the top of this document returns a feed that differs
from the anonymous one for a reader with reads.

### 3b — Expo shell

4. `mobile/` scaffold: the Expo config, `metro.config.js` (monorepo `watchFolders` + `nodeModulesPaths`),
   `babel.config.js`, expo-router `app/_layout.tsx`, a tab layout with one real tab and placeholders.
5. `@ih/core` as a declared dependency (the Phase 2 lesson — npm links only what a package asks for).
6. `mobile/design/`: tokens ported from `web/app/globals.css`'s real palette, not invented.

### 3c — Authentication

7. Native Google sign-in via `expo-auth-session` (Expo Go compatible) or
   `@react-native-google-signin` (needs a development build). **`expo-auth-session` first** — it
   works in Expo Go, which means testing needs no native build and no Xcode.
8. Exchange → `expo-secure-store` (iOS Keychain / Android Keystore).
9. `configureApi({ baseUrl, getToken: () => SecureStore.getItem(…) })` at app start.
10. Sign-out clears the token and calls `DELETE /api/me/tokens/:id`.

### 3d — Recommendations screen

11. One screen, `FlatList`, using `presentRecommendation`, `partitionByCountryMatch`, `countryName`,
    `makeT` + the shared catalogs. Country-matched cards first, backfill below a divider, exactly as
    the web page partitions them.
12. Feedback wired to `/api/me/recommendations/feedback` and `/opened` — already bearer-ready.

### 3e — Verification

13. **Headless, runs in CI:** a Node harness that drives `@ih/core`'s API client against the e2e
    stack with a real token and asserts a personalised feed, the country partition, and that every
    explanation resolves to a real catalog string. This tests everything except the native UI and
    Google's own sign-in, and it runs without a device.
14. **On-device checklist for you:** sign in with a real Google account, confirm the token lands in
    the keystore, confirm the feed differs from signed-out, confirm the Interest Intensity sliders
    set on web change the mobile feed.

## Recommendation

**Start at 3a, and do not start the Expo app first.** Building the shell against an API that returns
the demo reader's feed would mean the first screen demonstrates Interest Intensity, country
preference and viewpoint diversity for the wrong person — and it would look like it worked. 3a is
two small changes to routes whose shape is already decided, plus one new endpoint, and it is testable
end to end before a single line of React Native exists.
