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

`mobile/.env` — gitignored; copy `mobile/.env.example`. `app.config.ts` reads it at build time and
puts the values in `expo.extra`. Everything there is a public identifier (a native OAuth client has
no confidential half); they are kept out of git because they differ per deployment, not because they
are credentials. The one credential this app holds is the bearer token, minted at runtime into the
platform keystore.

```bash
cp mobile/.env.example mobile/.env
npm run verify:config --workspace @ih/mobile     # prints no values
```

| Variable | What it is |
|---|---|
| `EXPO_PUBLIC_API_BASE_URL` | the deployment. **`localhost` resolves to the phone on a real device** |
| `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID` | Google Console → OAuth client → iOS, needs the bundle identifier |
| `EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID` | → Android, needs the package name **and the SHA-1 of the signing certificate** |
| `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID` | the existing web client, which Expo's dev proxy uses |

The server needs the matching `GOOGLE_IOS_CLIENT_ID` / `GOOGLE_ANDROID_CLIENT_ID` (see
`web/.env.example`), or `/api/auth/mobile` answers `500 not-configured` and mints nothing.

**`docs/MOBILE_DEVICE_TEST.md` is the full walkthrough** — the console steps, the EAS build profiles
and the seven device checks. Two things from it worth knowing before you start: the Android SHA-1
comes from `eas credentials` and a missing one fails sign-in with a bare `DEVELOPER_ERROR` that never
mentions certificates; and the Google consent screen in Testing status is a second gate on top of
`BETA_ALLOWLIST`, so a tester must be on both lists.

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
