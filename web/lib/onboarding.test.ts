// The onboarding handoff — the stash an anonymous pick lives in until sign-in lands it (node --test).
//
// Small surface, load-bearing contract. `/signin/complete` decides between "persist this" and "carry
// on" purely from `readPendingOnboarding()`, and the whole architecture rests on that call being
// consuming and total: a malformed stash must read as "nothing to do" rather than throw (which would
// strand a reader on the interstitial) or return junk (which would POST junk to the engine).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PENDING_ONBOARDING_KEY,
  clearPendingOnboarding,
  readPendingOnboarding,
  stashPendingOnboarding,
} from "./onboarding.ts";

/** Install a minimal localStorage, seeded with `raw` under the pending key. */
function withStorage(raw: string | null, run: (store: Map<string, string>) => void): void {
  const g = globalThis as Record<string, unknown>;
  const prev = g.window;
  const store = new Map<string, string>();
  if (raw !== null) store.set(PENDING_ONBOARDING_KEY, raw);
  g.window = {
    localStorage: {
      getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    },
  };
  try {
    run(store);
  } finally {
    if (prev === undefined) delete g.window; else g.window = prev;
  }
}

test("a stashed selection round-trips", () => {
  withStorage(null, (store) => {
    stashPendingOnboarding(["nytimes", "foxnews", "reuters"]);
    assert.equal(store.get(PENDING_ONBOARDING_KEY), '{"outlets":["nytimes","foxnews","reuters"]}');
    assert.deepEqual(readPendingOnboarding(), ["nytimes", "foxnews", "reuters"]);
  });
});

test("clearing is consuming — which is what makes the sign-in landing loop-proof", () => {
  withStorage('{"outlets":["a"]}', () => {
    assert.deepEqual(readPendingOnboarding(), ["a"]);
    clearPendingOnboarding();
    // The landing page navigates to `/` after clearing. If the gate were ever to send the reader
    // back, there is nothing left to re-post, so the cycle terminates by construction.
    assert.equal(readPendingOnboarding(), null);
  });
});

test("nothing stashed reads as null (the returning-reader pass-through)", () => {
  withStorage(null, () => assert.equal(readPendingOnboarding(), null));
});

test("an unusable stash reads as null rather than throwing", () => {
  for (const raw of [
    "not json at all",
    "{}",                          // no outlets
    '{"outlets":[]}',              // empty selection: nothing to persist
    '{"outlets":"nytimes"}',       // wrong shape
    '{"outlets":[1,2,3]}',         // wrong member type
    '{"outlets":["",""]}',         // blank ids only
    "null",
  ]) {
    withStorage(raw, () => {
      assert.equal(readPendingOnboarding(), null, raw);
    });
  }
});

test("mixed members are filtered, not rejected wholesale", () => {
  withStorage('{"outlets":["nytimes",null,"","reuters",7]}', () => {
    assert.deepEqual(readPendingOnboarding(), ["nytimes", "reuters"]);
  });
});

test("storage failures are swallowed — a private-mode reader still gets through sign-in", () => {
  const g = globalThis as Record<string, unknown>;
  const prev = g.window;
  g.window = {
    localStorage: {
      getItem() { throw new Error("blocked"); },
      setItem() { throw new Error("blocked"); },
      removeItem() { throw new Error("blocked"); },
    },
  };
  try {
    assert.doesNotThrow(() => stashPendingOnboarding(["a"]));
    assert.equal(readPendingOnboarding(), null);
    assert.doesNotThrow(() => clearPendingOnboarding());
  } finally {
    if (prev === undefined) delete g.window; else g.window = prev;
  }
});
