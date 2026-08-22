// The app must register the URL scheme Google redirects to after sign-in.
//
// `expo-auth-session`'s Google provider builds its redirect URI as
// `${Application.applicationId}:/oauthredirect` (expo-auth-session/build/providers/Google.js) — the
// package name on Android and the bundle identifier on iOS, NOT the app's own deep-link scheme. So
// `com.hiddenview.app` has to appear in `expo.scheme` alongside `hiddenview`, or the operating
// system hands Google's redirect to nobody.
//
// This is guarded rather than trusted because the failure carries no error. The browser opens,
// Google accepts the sign-in, and then the screen just sits there: no crash, no message, nothing in
// a log. It looks exactly like a network problem, or a wrong client id, or a missing SHA-1 — three
// things you would check first, all of which take longer to rule out than this test takes to run.
//
// The original config had `scheme: "hiddenview"` alone. Prebuilding it and reading the generated
// native projects showed `<data android:scheme="hiddenview"/>` and a matching `CFBundleURLSchemes`
// with one entry: Expo's config plugin registers exactly what is listed and infers nothing from the
// package name. That would have failed on the first device test, after a twenty-minute cloud build.
import { test } from "node:test";
import assert from "node:assert/strict";

import config from "../app.config.ts";

const schemes = Array.isArray(config.scheme) ? config.scheme : [config.scheme];

test("the app id is registered as a URL scheme, because Google redirects to it", () => {
  const appId = config.ios?.bundleIdentifier;
  assert.ok(appId, "ios.bundleIdentifier must be set — it is half of the redirect URI");
  assert.ok(
    schemes.includes(appId),
    `expo.scheme must include "${appId}". expo-auth-session redirects to ` +
      `"${appId}:/oauthredirect"; a scheme the app does not claim silently goes nowhere. ` +
      `Currently: ${JSON.stringify(schemes)}`,
  );
});

test("the bundle identifier and the package name are the same string", () => {
  // One redirect URI serves both platforms only while these agree. If they diverge, each needs its
  // own scheme entry and its own Google OAuth client registration — and the divergence would show
  // up as sign-in working on one platform and hanging on the other.
  assert.equal(config.ios?.bundleIdentifier, config.android?.package);
});

test("the app keeps its own deep-link scheme as well", () => {
  // expo-router routes deep links through this one. Replacing it with the app id rather than adding
  // to it would fix sign-in and break every link into the app.
  assert.ok(schemes.includes("hiddenview"), `expo.scheme lost "hiddenview": ${JSON.stringify(schemes)}`);
  assert.equal(schemes[0], "hiddenview", "the router's scheme should stay first — it is the default");
});
