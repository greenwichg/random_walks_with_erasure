# `@ih/mobile` — Hidden View for Android and iOS

Expo / React Native, one codebase for both platforms. It reproduces the **mobile web app** (the web
app below `lg`) screen for screen — the same shell, the same cards, the same story page — as a new
client of the existing backend. The inspection, the component map and the reasons a few things
could not be carried across are in `docs/MOBILE_APP_PLAN.md`.

## Running it

```bash
npm install                                  # from the repo root; workspaces link @ih/core
cp mobile/.env.example mobile/.env           # then fill it in — see Configuration
npm run verify:config --workspace @ih/mobile # prints no values
```

The app uses native modules (`react-native-svg`, `expo-secure-store`, `expo-font`), so it runs in a
**development build**, not in Expo Go.

**Android emulator or device**

```bash
cd mobile
npx expo run:android          # builds the dev client and installs it on the running emulator / connected device
npx expo start --dev-client   # afterwards: the JS bundle, with fast refresh
```

**iPhone simulator or device** (macOS with Xcode)

```bash
cd mobile
npx expo run:ios              # simulator; add --device for a connected iPhone
npx expo start --dev-client
```

**Cloud builds** (no local toolchain): `npm run build:dev --workspace @ih/mobile` for the
development client, `build:preview` for an installable internal build — profiles in `eas.json`,
walkthrough in `docs/MOBILE_DEVICE_TEST.md`.

**Headless check** (what CI and this repository's own verification run):

```bash
npm run typecheck --workspace @ih/mobile
npm test --workspace @ih/mobile
npx expo export --platform android     # in mobile/ — bundles the whole app through Metro
npx expo export --platform ios
```

## What is built

| Screen | Route | Mirrors |
|---|---|---|
| Home | `/` | `home/home-mobile.tsx` — briefing, lens tabs, lead, rows, blind spots, local pulse, topic sections |
| Story | `/stories/[id]` | the mobile story page — hero / coverage masthead, then six collapsible sections: Story Intelligence · Breakdown (Bias · Factuality · Ownership) · How each side frames it · Coverage across publishers · Related Topics · Similar Stories |
| Stories | `/stories?…` | the Story browser with every filter, the tag chip and paging; Blind spots and Local in the tab bar are this screen |
| Search | `/search` | query, filters, article cards, paging |
| Publisher | `/publishers/[name]` | the full profile |
| For You | `/recommendations` | strategy tabs, consequence strips, cards with the feedback vocabulary, the country-backfill divider |
| Settings | `/settings` | every card the web has except the two that cannot exist on a phone (see the plan, §4) |
| Alerts | `/alerts` | the notification list; the header bell opens the same rows as a panel |
| Saved | `/saved` | |
| Menu | `/menu` | the full-screen directory; rows this build has no screen for open the web page in the in-app browser |
| Sign in | `/sign-in` | Google, then `POST /api/auth/mobile` |

Chrome on every screen: the masthead (menu · wordmark · bell · theme · account), the topic chip
strip, the utility strip, the footer, and the fixed bottom tab bar (Home · For You · Search · Blind
spots · Local). Safe areas (notch, Dynamic Island, home indicator) are handled by the header, the
tab bar and the screen container.

## Layout

| Directory | Holds |
|---|---|
| `app/` | Expo Router screens — routes are the web's paths, one for one |
| `components/` | native UI, one file per mobile-web component, same names (`layout/`, `shared/`, `stories/`, `home/`, `recommendations/`, `discover/`, `ui/`) |
| `design/` | tokens (the whole `globals.css` palette, checked against it by a test), the two typefaces |
| `lib/` | config, session (keystore), auth, the API + React Query hooks, i18n, theme, navigation, transports |
| `assets/fonts/` | Schibsted Grotesk and Instrument Sans, static instances (SIL OFL) |

## What is shared and what is not

**Nothing with a decision in it lives here.** Every screen reads `@ih/core`: `services` +
`queryKeys` over the shared axios client, the domain types, and `logic/*` (home derivations,
framing, story timeline, coverage groups, Tier-B split, the bias / factuality / ownership
distributions, interests, country partition, settings diff, notification kinds, the publisher-logo
walk). `lib/hooks.ts` mirrors `web/hooks/use-data.ts` hook for hook — same keys, same
invalidations, same optimistic saves. `lib/boundary.test.ts` forbids reaching into `web/` and
asserts the core imports are still there; `lib/catalog-keys.test.ts` asserts every catalog key the
app names exists.

The platform halves that DO live here:

| Here | Shared counterpart |
|---|---|
| `lib/i18n.ts` — device locale (the fallback before Settings load) | `@ih/core/i18n/core` — the resolver |
| `lib/session.ts` — the keystore | — |
| `lib/api.ts` — `baseUrl` + `getToken` | `@ih/core/api/client` |
| `lib/record-read.ts` — an authenticated POST | `@ih/core/logic/record-read` — the payload |
| `lib/navigation.ts` — web hrefs → native routes | — |
| `design/tokens.ts` — hex values | `web/app/globals.css` — the source of truth |

## Authentication

1. Google, on the device, returns an **ID token**. Android and iOS each present their own OAuth
   client id.
2. The app posts it to `POST /api/auth/mobile`.
3. The server verifies the signature against Google's published keys, checks the audience is one of
   ours, requires a verified email, runs the same closed-beta allowlist the web sign-in runs, and
   returns a Hidden View bearer token.
4. The token goes to `expo-secure-store` — the iOS Keychain, the Android Keystore — and the shared
   client attaches it to every request.

The app verifies nothing itself, on purpose. No secret ships in the app. Sign-out clears the
keystore; it does **not** revoke server-side (`/api/me/tokens/:id` is `SESSION_ONLY`, so a token
cannot revoke tokens) — revoke from the web.

## Configuration

`mobile/.env` — gitignored; copy `mobile/.env.example`. Every value is a public identifier.

| Variable | What it is |
|---|---|
| `EXPO_PUBLIC_API_BASE_URL` | the deployment. **`localhost` resolves to the phone on a real device** |
| `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID` | Google Console → OAuth client → iOS, needs the bundle identifier |
| `EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID` | → Android, needs the package name **and the SHA-1 of the signing certificate** |
| `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID` | the existing web client, which Expo's dev proxy uses |

The server needs the matching `GOOGLE_IOS_CLIENT_ID` / `GOOGLE_ANDROID_CLIENT_ID`, or
`/api/auth/mobile` answers `500 not-configured`.

## Not built, and why

- **Story Continuation** — built on browser return-visit mechanics (`sessionStorage`,
  `visibilitychange`); needs its own native design.
- **Browser-extension tokens in Settings** — `/api/me/tokens` is session-only by design.
- **Native push** — needs an APNs/FCM registration endpoint; the existing one takes a browser
  `PushSubscription`. The account-level "breaking on your devices" preference is present.
- **Report / Guide / Analytics / Analyze / Profile / History / Discover more / Privacy** — outside
  this build's scope; their menu rows open the web page in the in-app browser.
- **Sign in with Apple** — App Store Guideline 4.8 will require it alongside Google at review time.
