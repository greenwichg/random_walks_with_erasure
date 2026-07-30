// The onboarding handoff — the marker cookie the server-side gate reads (node --test).
//
// Small surface, load-bearing contract: the gate in `app/(app)/layout.tsx` looks the cookie up BY
// NAME, so a rename or a botched clear silently reopens the bug this whole mechanism exists to
// prevent (a reader who just finished the funnel being sent back through it). These tests pin the
// name, the TTL, and the fact that clearing actually expires.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PENDING_ONBOARDING_COOKIE,
  PENDING_ONBOARDING_KEY,
  clearOnboardingPending,
  markOnboardingPending,
} from "./onboarding.ts";

/** Capture what the helpers write to `document.cookie`, and clean up after. */
function withDocument(protocol: string, run: (writes: string[]) => void): void {
  const g = globalThis as Record<string, unknown>;
  const writes: string[] = [];
  const prevDoc = g.document;
  const prevLoc = g.location;
  g.document = { set cookie(v: string) { writes.push(v); }, get cookie() { return ""; } };
  g.location = { protocol };
  try {
    run(writes);
  } finally {
    if (prevDoc === undefined) delete g.document; else g.document = prevDoc;
    if (prevLoc === undefined) delete g.location; else g.location = prevLoc;
  }
}

test("the two halves of the handoff have distinct names (localStorage key vs cookie)", () => {
  assert.equal(PENDING_ONBOARDING_KEY, "ih:pendingOnboarding");
  // A cookie name may not contain a colon-bearing token from the storage key by accident; these are
  // separate namespaces and the gate imports the cookie one.
  assert.equal(PENDING_ONBOARDING_COOKIE, "ih_pending_onboarding");
});

test("marking pending writes a site-wide, lax, half-hour cookie", () => {
  withDocument("http:", (writes) => {
    markOnboardingPending();
    assert.equal(writes.length, 1);
    const c = writes[0]!;
    assert.match(c, new RegExp(`^${PENDING_ONBOARDING_COOKIE}=1;`));
    assert.match(c, /Path=\//);          // readable by the gate on every app route
    assert.match(c, /Max-Age=1800/);     // self-healing: an abandoned flush re-arms the gate
    assert.match(c, /SameSite=Lax/);     // survives the OAuth redirect back from the provider
    assert.doesNotMatch(c, /Secure/);    // plain-http dev would silently drop a Secure cookie
  });
});

test("over https the cookie is Secure", () => {
  withDocument("https:", (writes) => {
    markOnboardingPending();
    assert.match(writes[0]!, /; Secure$/);
  });
});

test("clearing expires the cookie immediately, under the same name and path", () => {
  withDocument("https:", (writes) => {
    clearOnboardingPending();
    const c = writes[0]!;
    assert.match(c, new RegExp(`^${PENDING_ONBOARDING_COOKIE}=;`));
    assert.match(c, /Path=\//);          // a different path would leave the original in place
    assert.match(c, /Max-Age=0/);
  });
});

test("both helpers are inert without a document (they are imported by a server module)", () => {
  const g = globalThis as Record<string, unknown>;
  assert.equal(g.document, undefined);   // the gate imports this module during SSR
  assert.doesNotThrow(() => markOnboardingPending());
  assert.doesNotThrow(() => clearOnboardingPending());
});
