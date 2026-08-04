// Story Continuation's browser state — the two of the design's nine gates that only the client
// holds: dismissal (§6.1) and the impression cap (§6.3). Run with `node --test`.
//
// The contract these tests defend is TOTALITY. Every function is parsed from storage a previous —
// possibly older — build wrote, and both callers have exactly two branches: render a strip, or
// render nothing. Junk must read as "nothing", never throw (which would break a card list) and
// never half-succeed (which would render a strip naming an empty publisher).
import { test } from "node:test";
import assert from "node:assert/strict";
import { currentAnalyticsProvider, flushAnalytics, setAnalyticsProvider } from "./analytics.ts";
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
  prefetchContinuation,
  recordImpression,
  subscribeArmed,
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

// ---------------------------------------------------------------- arming notifications
test("arming and clearing notify subscribers, and unsubscribe stops them", () => {
  withStorage(() => {
    const seen: string[] = [];
    const off = subscribeArmed(() => seen.push(readArmed()?.anchorUrl ?? "none"));
    armCandidate("https://a.example.com/1", OFFER);
    clearArmed();
    off();
    armCandidate("https://b.example.com/2", OFFER);
    assert.deepEqual(seen, ["https://a.example.com/1", "none"]);
  });
});

test("one throwing subscriber does not stop the others", () => {
  // Sixty cards subscribe. One unmounting mid-notify must not silence the card that matters.
  withStorage(() => {
    const seen: number[] = [];
    const offA = subscribeArmed(() => {
      throw new Error("boom");
    });
    const offB = subscribeArmed(() => seen.push(1));
    armCandidate("https://a.example.com/1", OFFER);
    offA();
    offB();
    assert.deepEqual(seen, [1]);
  });
});

// ---------------------------------------------------------------- the prefetch (§10.2)
test("prefetch arms on an offer, and stays silent on every failure", async () => {
  // Storage is installed for the whole async body rather than via withStorage: the promise chain
  // resolves AFTER a synchronous helper's teardown would have removed `window`, and the first draft
  // of this test failed for exactly that reason rather than for anything about the code.
  const g = globalThis as Record<string, unknown>;
  const prevWindow = g.window;
  const prevFetch = g.fetch;
  const calls: string[] = [];

  const cases: Array<[string, () => Promise<unknown>, boolean]> = [
    ["an offer", async () => ({ ok: true, json: async () => OFFER }), true],
    ["a null body", async () => ({ ok: true, json: async () => null }), false],
    [
      "an offer with no sibling url",
      async () => ({ ok: true, json: async () => ({ storyId: "s" }) }),
      false,
    ],
    // These two return a VALID body on purpose: the contract is that a non-2xx is never trusted,
    // and a fake that also returned null would let a missing `r.ok` check pass unnoticed.
    ["a 401 with a body", async () => ({ ok: false, status: 401, json: async () => OFFER }), false],
    ["a 503 with a body", async () => ({ ok: false, status: 503, json: async () => OFFER }), false],
    [
      "a network throw",
      async () => {
        throw new Error("offline");
      },
      false,
    ],
  ];

  try {
    for (const [label, impl, shouldArm] of cases) {
      const m = new Map<string, string>();
      const store = {
        getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
        setItem: (k: string, v: string) => void m.set(k, v),
        removeItem: (k: string) => void m.delete(k),
      };
      g.window = { sessionStorage: store, localStorage: store };
      g.fetch = (u: string) => {
        calls.push(u);
        return impl();
      };
      // The click handler never awaits this — the publisher's tab must open at once.
      assert.doesNotThrow(() => prefetchContinuation("https://cbs.example.com/a"));
      await new Promise((r) => setTimeout(r, 0));
      // Assert on what was WRITTEN, not on readArmed(): readArmed does its own shape validation,
      // which would mask a missing guard here and let both of these mutations pass.
      assert.equal(m.has(ARMED_KEY), shouldArm, `wrote an armed candidate after ${label}`);
      assert.equal(readArmed() !== null, shouldArm, `arming after ${label}`);
    }
  } finally {
    if (prevWindow === undefined) delete g.window;
    else g.window = prevWindow;
    if (prevFetch === undefined) delete g.fetch;
    else g.fetch = prevFetch;
  }

  assert.equal(calls.length, cases.length);
  assert.ok(calls[0].startsWith("/api/me/continuation?url="), "same-origin proxy, url-encoded");
  assert.ok(calls[0].includes(encodeURIComponent("https://cbs.example.com/a")));
});

test("eligible and armed are separate events, and the gap is client-side loss", async () => {
  // Drives the REAL prefetchContinuation, not a re-implementation of it. The gap between the two
  // events IS the loss measurement (§7): if both fired unconditionally, a reader whose storage
  // refused would look identical to one who was armed, and the only reason the two exist
  // separately would be gone.
  const g = globalThis as Record<string, unknown>;
  const prevWindow = g.window;
  const prevFetch = g.fetch;
  const prevProvider = currentAnalyticsProvider();
  const seen: string[] = [];
  setAnalyticsProvider({ name: "test", send: (e) => void e.forEach((x) => seen.push(x.event)) });
  flushAnalytics(); // drain anything an earlier test in this process left buffered
  seen.length = 0;
  g.fetch = async () => ({ ok: true, json: async () => OFFER });

  const working = new Map<string, string>();
  const refuse = () => {
    throw new Error("SecurityError");
  };
  const stores: Array<[string, unknown, string[]]> = [
    [
      "storage works",
      {
        getItem: (k: string) => (working.has(k) ? working.get(k)! : null),
        setItem: (k: string, v: string) => void working.set(k, v),
        removeItem: (k: string) => void working.delete(k),
      },
      ["continuation_eligible", "continuation_armed"],
    ],
    [
      "storage refuses",
      { getItem: refuse, setItem: refuse, removeItem: refuse },
      ["continuation_eligible"],
    ],
  ];

  try {
    for (const [label, store, expected] of stores) {
      seen.length = 0;
      working.clear();
      g.window = { sessionStorage: store, localStorage: store };
      prefetchContinuation("https://cbs.example.com/a");
      await new Promise((r) => setTimeout(r, 0));
      flushAnalytics();
      assert.deepEqual(seen, expected, label);
    }
  } finally {
    setAnalyticsProvider(prevProvider);
    if (prevWindow === undefined) delete g.window;
    else g.window = prevWindow;
    if (prevFetch === undefined) delete g.fetch;
    else g.fetch = prevFetch;
  }
});
