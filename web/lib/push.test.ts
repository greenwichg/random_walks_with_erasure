import { test } from "node:test";
import assert from "node:assert/strict";
import {
  resolveLang,
  urlBase64ToUint8Array,
  serializeSubscription,
  normalizePermission,
  pushUiState,
  shouldRepairSubscription,
  subscriptionMatchesKey,
  type PushCapabilities,
} from "./push.ts";

// A real VAPID public key shape: 65 bytes, uncompressed P-256 point, base64url, unpadded.
const VAPID =
  "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U";

// --------------------------------------------------------------------------------------------- //
// §4 — the language fallback ORDER, which is the whole point of the function.
// --------------------------------------------------------------------------------------------- //
test("the stored language wins over the payload's", () => {
  // The payload's language was captured at SEND time; a push can sit under its TTL for hours, so the
  // device's own value is the one that is correct when it finally renders.
  assert.equal(resolveLang("es", "en"), "es");
});

test("the payload's language is used when nothing is stored", () => {
  // Cleared site data, a restored device, a subscription that outlived the store.
  assert.equal(resolveLang(undefined, "fr"), "fr");
  assert.equal(resolveLang(null, "de"), "de");
  assert.equal(resolveLang("", "pt"), "pt");
});

test("an unsupported value at either level falls through rather than being rendered", () => {
  assert.equal(resolveLang("kl", "fr"), "fr", "a stored language we do not ship is skipped");
  assert.equal(resolveLang("kl", "xx"), "en");
  assert.equal(resolveLang(42, { lang: "es" }), "en", "non-strings are not languages");
});

test("with nothing anywhere, the platform default renders", () => {
  assert.equal(resolveLang(), "en");
});

// --------------------------------------------------------------------------------------------- //
// The VAPID key conversion — it fails LATE (a subscribe() rejection at permission time), so it is
// tested here rather than discovered there.
// --------------------------------------------------------------------------------------------- //
test("a real VAPID public key converts to 65 bytes starting with the uncompressed-point marker", () => {
  const bytes = urlBase64ToUint8Array(VAPID);
  assert.equal(bytes.length, 65, "uncompressed P-256 point");
  assert.equal(bytes[0], 0x04, "uncompressed-point prefix");
});

test("base64url's substituted characters are translated back", () => {
  // "-" and "_" stand in for "+" and "/". Left untranslated, atob either throws or silently yields
  // different bytes — and the resulting key is rejected only when the browser tries to use it.
  assert.deepEqual(Array.from(urlBase64ToUint8Array("-_-_")), Array.from(atob("+/+/"), (c) => c.charCodeAt(0)));
});

test("an unpadded key decodes — which is the common case for VAPID", () => {
  // No padding step in the implementation: `atob` is forgiving-base64 decode and accepts a missing
  // tail. These pin that, so removing the reliance would fail here rather than in a browser.
  assert.deepEqual(Array.from(urlBase64ToUint8Array("QQ")), [65]);
  assert.deepEqual(Array.from(urlBase64ToUint8Array("QUI")), [65, 66]);
  assert.deepEqual(Array.from(urlBase64ToUint8Array("QUJD")), [65, 66, 67]);
});

test("a padded key decodes to the same bytes as its unpadded form", () => {
  assert.deepEqual(Array.from(urlBase64ToUint8Array("QUI=")), [65, 66]);
  assert.deepEqual(Array.from(urlBase64ToUint8Array("QUI=")), Array.from(urlBase64ToUint8Array("QUI")));
});

test("malformed base64 throws rather than yielding wrong bytes", () => {
  // A key that cannot be decoded must fail loudly here. Silently producing *some* bytes would create
  // a subscription against a key nothing can sign for, and the symptom would appear at send time.
  assert.throws(() => urlBase64ToUint8Array("QUJD="), "an over-padded group is not decodable");
  assert.throws(() => urlBase64ToUint8Array("!!!!"));
});

test("surrounding whitespace does not corrupt the key", () => {
  // An operator copying a key out of a terminal is the expected source of this.
  assert.deepEqual(Array.from(urlBase64ToUint8Array("  QUJD \n")), [65, 66, 67]);
});

test("an empty key throws rather than producing an empty array", () => {
  // Silently subscribing with a zero-length key yields an endpoint nothing can ever send to.
  assert.throws(() => urlBase64ToUint8Array(""));
  assert.throws(() => urlBase64ToUint8Array("   "));
});

// --------------------------------------------------------------------------------------------- //
// Serialization — all-or-nothing, because a partial body is a 422 the reader cannot act on.
// --------------------------------------------------------------------------------------------- //
test("a complete subscription flattens to the API's shape", () => {
  const got = serializeSubscription(
    { endpoint: "https://fcm.example/x", expirationTime: null, keys: { p256dh: "BKey", auth: "Auth" } },
    "Mozilla/5.0",
  );
  assert.deepEqual(got, {
    endpoint: "https://fcm.example/x",
    p256dh: "BKey",
    auth: "Auth",
    expirationTime: null,
    userAgent: "Mozilla/5.0",
  });
});

test("a subscription missing any required part serializes to null, not a partial body", () => {
  const full = { endpoint: "https://fcm.example/x", keys: { p256dh: "BKey", auth: "Auth" } };
  assert.equal(serializeSubscription(null), null);
  assert.equal(serializeSubscription(undefined), null);
  assert.equal(serializeSubscription({ ...full, endpoint: "" }), null);
  assert.equal(serializeSubscription({ ...full, keys: { auth: "Auth" } }), null);
  assert.equal(serializeSubscription({ ...full, keys: { p256dh: "BKey" } }), null);
  assert.equal(serializeSubscription({ ...full, keys: {} }), null);
});

test("expirationTime is preserved when the browser supplies one", () => {
  const got = serializeSubscription({
    endpoint: "https://fcm.example/x",
    expirationTime: 1_800_000_000_000,
    keys: { p256dh: "BKey", auth: "Auth" },
  });
  assert.equal(got?.expirationTime, 1_800_000_000_000);
});

test("a long user agent is bounded to what the column accepts", () => {
  const got = serializeSubscription(
    { endpoint: "https://fcm.example/x", keys: { p256dh: "BKey", auth: "Auth" } },
    "U".repeat(400),
  );
  assert.equal(got?.userAgent?.length, 255);
});

// --------------------------------------------------------------------------------------------- //
// Capability derivation — four signals, and the one that most needs naming is `blocked`.
// --------------------------------------------------------------------------------------------- //
const CAPS: PushCapabilities = {
  supported: true,
  configured: true,
  permission: "default",
  subscribed: false,
  hasSubscription: false,
};

test("permission values outside the platform's three are treated as unsupported", () => {
  assert.equal(normalizePermission("granted"), "granted");
  assert.equal(normalizePermission("denied"), "denied");
  assert.equal(normalizePermission("default"), "default");
  assert.equal(normalizePermission(undefined), "unsupported");
  assert.equal(normalizePermission("GRANTED"), "unsupported");
});

test("a denied permission is BLOCKED, never merely off", () => {
  // Once denied, requestPermission() is a permanent no-op, so an "off" control would be a button that
  // cannot work. The reader has to change it in browser settings and the UI must say so.
  assert.equal(pushUiState({ ...CAPS, permission: "denied" }), "blocked");
  assert.equal(pushUiState({ ...CAPS, permission: "denied", subscribed: true }), "blocked");
});

test("push is unavailable when the browser lacks it or the server has not configured it", () => {
  assert.equal(pushUiState({ ...CAPS, supported: false }), "unavailable");
  assert.equal(pushUiState({ ...CAPS, configured: false }), "unavailable");
  assert.equal(pushUiState({ ...CAPS, permission: "unsupported" }), "unavailable");
  assert.equal(
    pushUiState({
      supported: false,
      configured: false,
      permission: "granted",
      subscribed: true,
      hasSubscription: true,
    }),
    "unavailable",
    "an unsupported BROWSER is unavailable even holding a subscription — nothing can be done there",
  );
});

test("a still-registered device on a rolled-back deployment is PAUSED, not hidden", () => {
  // P4. The row survives the rollback by design and the API keeps deletion open for exactly that
  // reason — so reporting "unavailable" here (which hides the control) is what stranded the reader
  // with a registration they could not remove.
  assert.equal(
    pushUiState({ ...CAPS, configured: false, permission: "granted", hasSubscription: true }),
    "paused",
  );
});

test("a device that is NOT registered on a rolled-back deployment stays hidden", () => {
  // Nothing to remove, so nothing to show: the control would be a switch with no destination.
  assert.equal(pushUiState({ ...CAPS, configured: false, hasSubscription: false }), "unavailable");
});

test("paused outranks blocked, because removal must stay reachable either way", () => {
  // A reader who revoked permission AND is on a rolled-back deployment still has a row in the engine.
  // "blocked" disables the control; "paused" is the state that can still delete.
  assert.equal(
    pushUiState({ ...CAPS, configured: false, permission: "denied", hasSubscription: true }),
    "paused",
  );
});

test("on requires BOTH a granted permission and a live subscription", () => {
  assert.equal(pushUiState({ ...CAPS, permission: "granted", subscribed: true }), "on");
  assert.equal(pushUiState({ ...CAPS, permission: "granted", subscribed: false }), "off");
  // Permission granted on another device, or granted then unsubscribed here: not on.
  assert.equal(pushUiState({ ...CAPS, permission: "default", subscribed: true }), "off");
});

// --------------------------------------------------------------------------------------------- //
// Silent repair after a VAPID rotation (P2). The decision is a pure predicate precisely because it
// runs with no reader present — it must never be able to subscribe someone who did not ask.
// --------------------------------------------------------------------------------------------- //
const REPAIR = {
  supported: true,
  configured: true,
  permission: "granted" as const,
  hasSubscription: true,
  keyMatches: false,
};

test("a subscription bound to a retired key IS repaired", () => {
  // The regression this exists for: before it, a rotation left the device dark. Nothing sent, nothing
  // logged, and a toggle that silently switched itself off was the only symptom.
  assert.equal(shouldRepairSubscription(REPAIR), true);
});

test("a subscription the engine has forgotten is repaired, even with a matching key", () => {
  // The second way the two sides desynchronise, and the more common one: a `410` prunes the row on
  // the server (ordinary attrition) and the browser is never told. The key still matches, so the
  // rotation repair does not fire, and every signal the device has says it is subscribed — while
  // nothing can reach the reader. Seen in production during the first end-to-end test.
  assert.equal(shouldRepairSubscription({ ...REPAIR, keyMatches: true }), false);
  assert.equal(
    shouldRepairSubscription({ ...REPAIR, keyMatches: true, knownToServer: false }),
    true,
  );
});

test("an unanswerable server check never triggers a re-subscribe", () => {
  // `undefined` is "not established", not "absent". Collapsing the two would make every reader
  // re-subscribe whenever the engine hiccuped — turning a transient fault into a write storm against
  // the push service, which is the failure this whole area exists to avoid.
  assert.equal(
    shouldRepairSubscription({ ...REPAIR, keyMatches: true, knownToServer: undefined }),
    false,
  );
});

test("a device that never subscribed is never subscribed FOR the reader", () => {
  // Repair restores what a reader chose; it must not choose for them. This is the guard that keeps a
  // silent, promptless operation honest.
  assert.equal(shouldRepairSubscription({ ...REPAIR, hasSubscription: false }), false);
});

test("repair never runs without granted permission", () => {
  // Re-subscribing needs no prompt only because consent already exists. Without it this would be an
  // attempt to subscribe someone who has not agreed — and on "denied" it cannot succeed anyway.
  for (const permission of ["default", "denied", "unsupported"] as const) {
    assert.equal(shouldRepairSubscription({ ...REPAIR, permission }), false, permission);
  }
});

test("a matching key is left alone", () => {
  // Churning the endpoint for no reason costs the engine a row to reconcile on every page load.
  assert.equal(shouldRepairSubscription({ ...REPAIR, keyMatches: true }), false);
});

test("repair does not run when the browser or the deployment cannot support push", () => {
  assert.equal(shouldRepairSubscription({ ...REPAIR, supported: false }), false);
  assert.equal(shouldRepairSubscription({ ...REPAIR, configured: false }), false);
});

// --------------------------------------------------------------------------------------------- //
// Key rotation — a subscription created against the old pair can never be sent to again.
// --------------------------------------------------------------------------------------------- //
test("a subscription created against the server's current key matches", () => {
  const key = urlBase64ToUint8Array(VAPID);
  assert.equal(subscriptionMatchesKey(key.buffer, VAPID), true);
});

test("a subscription from a rotated-away key does not match", () => {
  // The device cannot detect a rotation on its own; this comparison is how it finds out that its
  // endpoint has become undeliverable and it must re-subscribe.
  const other = urlBase64ToUint8Array(VAPID);
  other[10] ^= 0xff;
  assert.equal(subscriptionMatchesKey(other.buffer, VAPID), false);
});

test("a key that is a strict PREFIX of the server's does not match", () => {
  // The case an element-wise comparison alone gets wrong: `Array.every` over a shorter array returns
  // true, because every element it has does match. Without the length check this reports a match and
  // the device never re-subscribes, so it silently stays undeliverable.
  const truncated = urlBase64ToUint8Array(VAPID).slice(0, 10);
  assert.equal(subscriptionMatchesKey(truncated.buffer as ArrayBuffer, VAPID), false);
});

test("a missing key, a length mismatch, or an unreadable server key never reports a match", () => {
  assert.equal(subscriptionMatchesKey(null, VAPID), false);
  assert.equal(subscriptionMatchesKey(undefined, VAPID), false);
  assert.equal(subscriptionMatchesKey(new Uint8Array([4, 5]).buffer, VAPID), false);
  assert.equal(subscriptionMatchesKey(urlBase64ToUint8Array(VAPID).buffer, ""), false);
  assert.equal(subscriptionMatchesKey(urlBase64ToUint8Array(VAPID).buffer, "!!!not-base64!!!"), false);
});
