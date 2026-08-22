# `@ih/mobile` — Hidden View for Android and iOS

Expo / React Native. **One real screen so far — Recommendations — plus sign-in.** The rest of the
app is deliberately not built yet.

## Running it

```bash
npm install                    # from the repo root; workspaces link @ih/core
npm start --workspace @ih/mobile
```

Before it can sign anyone in, two things have to be filled in — see **Configuration** below. Until
they are, the sign-in screen says so on screen rather than failing on tap.

## Layout

| Directory | Holds |
|---|---|
| `app/` | Expo Router screens — `_layout.tsx`, `index.tsx` (Recommendations), `sign-in.tsx` |
| `components/` | native UI |
| `design/` | tokens, transcribed from `web/app/globals.css` and checked against it by a test |
| `lib/` | config, session (keystore), auth, the API and i18n wiring |

## What is shared and what is not

**Nothing with a decision in it lives here.** The Recommendations screen imports
`services.recommendations()`, `partitionByCountryMatch`, `countryName` and `presentRecommendation`
from `@ih/core` — the same four modules `web/app/(app)/recommendations/page.tsx` imports. The screen
decides layout; the shared core decides everything a reader could disagree with.

`presentRecommendation` returns catalog **keys**, never rendered strings, so a card's explanation is
the same sentence on both platforms because it is the same function over the same catalog — not
because two implementations were kept in step. `lib/boundary.test.ts` asserts those imports are still
there, and `lib/catalog-keys.test.ts` asserts every key the app names actually exists (a missing one
renders as the key itself, which typecheck and lint both wave through).

The platform halves that DO live here are the ones the Phase 2 split created seams for:

| Here | Shared counterpart |
|---|---|
| `lib/i18n.ts` — device locale | `@ih/core/i18n/core` — the resolver |
| `lib/session.ts` — the keystore | — |
| `lib/api.ts` — `baseUrl` + `getToken` | `@ih/core/api/client` — the axios instance |
| `design/tokens.ts` — hex values | `web/app/globals.css` — the source of truth |

## Authentication

1. Google, on the device, returns an **ID token**.
2. The app posts it to `POST /api/auth/mobile`.
3. The server verifies the signature against Google's published keys, checks the audience is one of
   ours, requires a verified email, runs the same closed-beta allowlist the web sign-in runs, and
   returns a Hidden View bearer token.
4. The token goes to `expo-secure-store` — the iOS Keychain, the Android Keystore.

The app verifies nothing itself, on purpose: a client that decided whether its own credential was
valid is a client an attacker can patch. The Google ID token is used once and never stored. No
secret ships in the app — native OAuth clients do not have one, which is why this shape is standard
rather than a workaround.

Sign-out clears the keystore. It does **not** revoke server-side, because `/api/me/tokens/:id` is
`SESSION_ONLY` and this token cannot reach it — a deliberate consequence of a token being unable to
revoke tokens, which is what stops a stolen one locking its owner out. Revoke from the web.

## Configuration

`app.json` → `expo.extra`. Everything there is a public identifier; the bearer token is minted at
runtime and lives in the keystore.

| Key | What it is |
|---|---|
| `apiBaseUrl` | the deployment. **`localhost` resolves to the phone on a real device** — use the LAN address or a tunnel |
| `googleIosClientId` | Google Cloud Console → OAuth client → iOS, needs the bundle identifier |
| `googleAndroidClientId` | → Android, needs the package name **and the SHA-1 of the signing certificate** |
| `googleWebClientId` | the existing web client, which Expo's dev proxy uses |

The server needs the matching `GOOGLE_IOS_CLIENT_ID` / `GOOGLE_ANDROID_CLIENT_ID` (see
`web/.env.example`), or `/api/auth/mobile` answers `500 not-configured` and mints nothing.

**The Android SHA-1 is the step that stalls this work.** Register both the debug keystore and the
EAS release keystore (`eas credentials`); a missing fingerprint fails sign-in on the device with a
bare `DEVELOPER_ERROR` that never mentions certificates.

**While the OAuth consent screen is in Testing**, only listed test users can sign in (max 100) —
a second gate, independent of `BETA_ALLOWLIST`. A tester missing from either is refused.

## What is verified, and what is not

`npm test --workspace @ih/mobile` runs the boundary guard, the catalog-key check and the design
tokens. `web/e2e/specs/mobile-shared-core.spec.ts` drives the whole Recommendations path against the
real stack with a real bearer token — the feed, the settings, the country partition, and that every
explanation resolves to a real sentence.

**Not verified here, and needing a device:** Google's native sign-in, the keystore, and how any of it
looks. There is no simulator in the development container. `docs/MOBILE_PHASE3_PLAN.md` carries the
on-device checklist.

## Not built yet

Every other screen; APNs/FCM push (which needs its own registration endpoint — the existing
`/api/push/subscriptions` takes a browser `PushSubscription` and cannot represent a device token);
Sign in with Apple, which App Store Guideline 4.8 will require alongside Google at review time.
