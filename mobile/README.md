# `@ih/mobile` — Hidden View for Android and iOS

**Nothing is implemented here yet.** This directory is a boundary, created with the `packages/core`
split so that the question "where does this go?" has an answer before anyone has to guess.

## Why it is empty on purpose

The split's whole value is that the answer to that question is structural rather than remembered.
Creating `mobile/` at the same time as `packages/core` — rather than when the first screen is
written — is what makes the boundary real while the shared core is being filled: a module that would
have to live in both `web/` and `mobile/` is a module that belongs in `packages/core`, and that test
only works if `mobile/` exists to fail it.

There are no `start`, `build` or `test` scripts. A CI job for a directory with nothing in it is a red
X that teaches people to ignore red Xs.

## What goes here, when the time comes

| Directory | Holds |
|---|---|
| `app/` | Expo Router screens and layouts |
| `components/` | native UI — `View`, `Text`, `Pressable`, gesture and animation code |
| `design/` | the mobile design system: tokens, typography, spacing, theming |

Also here, and nowhere else: navigation, `expo-secure-store` (where the bearer token lives), APNs and
FCM registration, native share and deep linking, and anything that imports from `react-native` or
`expo`.

## What does NOT go here

Anything the web app would also want. If a rule about the product is true on both platforms, it
belongs in `@ih/core` — see `packages/core/README.md`. The reverse is enforced: `@ih/core`'s guard
test bans `react-native` and `expo` imports outright, so the shared core cannot quietly become
mobile-flavoured.

## What is already waiting

`@ih/core` holds the type contract, the product logic, the message catalogs and the typed API client.
The client is already configured rather than environment-driven, which is the seam this app needs:

```ts
import { configureApi } from "@ih/core/api/client";
import * as SecureStore from "expo-secure-store";

configureApi({
  baseUrl: "https://hidden-view.com",
  getToken: () => SecureStore.getItem("ih.token"),
});
```

After that, every function in `@ih/core/api/services` works from this app exactly as it does from the
browser.

## Before the first screen

Two prerequisites, both tracked in `docs/API_AUTH_MATRIX.md`:

1. **Bearer auth on the routes this app will actually read.** Phase 1 covered all of `/api/me/*`.
   Eight handlers across seven files are still session-only and are the blocking set: `/api/dashboard`,
   `/api/report`, `/api/recommendations`, `/api/history`, `/api/analytics`, `/api/profile`, and
   `/api/settings` (GET + POST). Three of those serve the **demo reader** to an unauthenticated
   caller rather than a 401, so a mobile client would render someone else's numbers with no error.
2. **A mobile sign-in flow.** Today a token is minted from an already-signed-in browser session at
   `/api/me/tokens`, which is right for the browser extension and is not a sign-up flow. Native OAuth
   → token exchange is its own design.
