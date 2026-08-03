// Story Continuation's browser state — the two of the design's nine gates that only the client
// holds: dismissal (§6.1) and the impression cap (§6.3). Run with `node --test`.
//
// The contract these tests defend is TOTALITY. Every function is parsed from storage a previous —
// possibly older — build wrote, and both callers have exactly two branches: render a strip, or
// render nothing. Junk must read as "nothing", never throw (which would break a card list) and
// never half-succeed (which would render a strip naming an empty publisher).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ARMED_KEY,
  MAX_IMPRESSIONS,
  PRUNE_AFTER_MS,
  STATE_KEY,
  armCandidate,
  clearArmed,
  dismissStory,
  mayShow,
  readArmed,
  readState,
  recordImpression,
} from "./continuation.ts";

const OFFER = {
  storyId: "s-harbor",
  storyTitle: "Harbor bridge oversight ruling",
  outlets: 9,
  anchor: { url: "https://cbs.example.com/a", publisher: "CBS News", lean: -1, leanBucket: "left" },
  sibling: {
    url: "https://fox.example.com/b",
    publisher: "Fox News",
    headline: "Ruling lands",
    lean: 2,
    leanBucket: "right",
    publishedAt: "2026-08-03T09:00:00Z",
  },
  distance: 3,
  candidateCount: 5,
} as unknown as import("../types/domain.ts").Continuation;

/** A minimal window with both storages, matching lib/onboarding.test.ts's approach. */
function withStorage(
  run: (seed: (store: "session" | "local", key: string, raw: string) => void) => void,
  { throwing = false }: { throwing?: boolean } = {},
): void {
  const g = globalThis as Record<string, unknown>;
  const prev = g.window;
  const make = (m: Map<string, string>) =>
    throwing
      ? {
          getItem: () => {
            throw new Error("SecurityError");
          },
          setItem: () => {
            throw new Error("SecurityError");
          },
          removeItem: () => {
            throw new Error("SecurityError");
          },
        }
      : {
          getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
          setItem: (k: string, v: string) => void m.set(k, v),
          removeItem: (k: string) => void m.delete(k),
        };
  const s = new Map<string, string>();
  const l = new Map<string, string>();
  g.window = { sessionStorage: make(s), localStorage: make(l) };
  try {
    run((store, key, raw) => void (store === "session" ? s : l).set(key, raw));
  } finally {
    if (prev === undefined) delete g.window;
    else g.window = prev;
  }
}

// ---------------------------------------------------------------- the armed candidate (session)
test("armed candidate round-trips an offer", () => {
  withStorage(() => {
    armCandidate("https://cbs.example.com/a", OFFER, 1_000);
    const armed = readArmed();
    assert.equal(armed?.anchorUrl, "https://cbs.example.com/a");
    assert.equal(armed?.armedAt, 1_000);
    assert.equal(armed?.offer.sibling.publisher, "Fox News");
  });
});

test("a second read supersedes the first rather than stacking", () => {
  withStorage(() => {
    armCandidate("https://a.example.com/1", OFFER, 1_000);
    armCandidate("https://b.example.com/2", { ...OFFER, storyId: "s-two" }, 2_000);
    assert.equal(readArmed()?.anchorUrl, "https://b.example.com/2");
    assert.equal(readArmed()?.offer.storyId, "s-two");
  });
});

test("clearArmed disarms, and nothing armed reads as null", () => {
  withStorage(() => {
    assert.equal(readArmed(), null);
    armCandidate("https://a.example.com/1", OFFER);
    clearArmed();
    assert.equal(readArmed(), null);
  });
});

test("a malformed or half-populated armed candidate reads as null", () => {
  // Rendering a strip from a partial offer would name an empty publisher — worse than no strip.
  for (const raw of [
    "{not json",
    '"a string"',
    JSON.stringify({ armedAt: 1, offer: OFFER }), // no anchorUrl
    JSON.stringify({ anchorUrl: "u", armedAt: 1 }), // no offer
    JSON.stringify({ anchorUrl: "u", armedAt: "soon", offer: OFFER }), // armedAt not a number
    JSON.stringify({ anchorUrl: "u", armedAt: 1, offer: { storyId: "s" } }), // no sibling
    JSON.stringify({ anchorUrl: "u", armedAt: 1, offer: { storyId: "s", sibling: {} } }), // no url
  ]) {
    withStorage((seed) => {
      seed("session", ARMED_KEY, raw);
      assert.equal(readArmed(), null, `expected null for ${raw.slice(0, 40)}`);
    });
  }
});

// ---------------------------------------------------------------- dismissal + cap (local)
test("a story nobody has seen may be shown", () => {
  withStorage(() => assert.equal(mayShow("s-new"), true));
});

test("dismissal is permanent, and scoped to that story", () => {
  withStorage(() => {
    dismissStory("s-harbor", 1_000);
    assert.equal(mayShow("s-harbor", 1_000), false);
    // "Permanently" has to outlive the session, or it is nagging by another name.
    assert.equal(mayShow("s-harbor", 1_000 + 29 * 24 * 3600_000), false);
    assert.equal(mayShow("s-other", 1_000), true);
  });
});

test("the impression cap stops the offer after MAX_IMPRESSIONS", () => {
  withStorage(() => {
    assert.equal(recordImpression("s-harbor"), 1);
    assert.equal(mayShow("s-harbor"), true);
    assert.equal(recordImpression("s-harbor"), 2);
    assert.equal(mayShow("s-harbor"), false);
  });
  assert.equal(MAX_IMPRESSIONS, 2);
});

test("dismissing later keeps the impression count", () => {
  withStorage(() => {
    recordImpression("s-harbor", 1_000);
    dismissStory("s-harbor", 2_000);
    const e = readState(2_000)["s-harbor"];
    assert.equal(e.n, 1);
    assert.equal(e.d, 1);
  });
});

test("entries older than 30 days are pruned on read, and stop suppressing", () => {
  withStorage(() => {
    const now = 1_000_000_000_000;
    dismissStory("s-old", now - PRUNE_AFTER_MS - 1);
    dismissStory("s-recent", now - 1_000);
    assert.deepEqual(Object.keys(readState(now)), ["s-recent"]);
    // Pruning on READ is what keeps the key bounded without a separate sweep — so the write must
    // have happened too, not just the filtering of the returned value.
    assert.deepEqual(Object.keys(JSON.parse(window.localStorage.getItem(STATE_KEY) ?? "{}")), [
      "s-recent",
    ]);
    assert.equal(mayShow("s-old", now), true); // 30 days is the whole promise
  });
});

test("malformed persisted state reads as no recorded state", () => {
  for (const raw of [
    "{not json",
    "[]",
    JSON.stringify({ "s-x": { n: 1 } }), // no timestamp
    JSON.stringify({ "s-x": 5 }), // not an object
    JSON.stringify({ "s-x": null }),
  ]) {
    withStorage((seed) => {
      seed("local", STATE_KEY, raw);
      assert.equal(mayShow("s-x"), true, `expected permissive for ${raw.slice(0, 30)}`);
    });
  }
});

// ---------------------------------------------------------------- storage that refuses
test("storage that throws degrades to 'no strip', never to an exception", () => {
  // Private mode / disabled storage. The feature must quietly not appear, never break the page —
  // and "not appear" is the same outcome an ineligible read already produces.
  withStorage(
    () => {
      assert.doesNotThrow(() => armCandidate("https://a.example.com/1", OFFER));
      assert.equal(readArmed(), null);
      assert.doesNotThrow(() => clearArmed());
      assert.doesNotThrow(() => dismissStory("s-x"));
      assert.doesNotThrow(() => recordImpression("s-x"));
      assert.equal(mayShow("s-x"), true);
    },
    { throwing: true },
  );
});

test("every helper is safe on the server, where there is no window", () => {
  const g = globalThis as Record<string, unknown>;
  const prev = g.window;
  delete g.window;
  try {
    assert.doesNotThrow(() => armCandidate("https://a.example.com/1", OFFER));
    assert.equal(readArmed(), null);
    assert.deepEqual(readState(), {});
    assert.equal(mayShow("s-x"), true);
  } finally {
    if (prev !== undefined) g.window = prev;
  }
});
