import { test, before } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

/**
 * The service worker's RENDER decision (Phase B2), driven without a browser.
 *
 * `public/sw.js` is served verbatim and cannot be imported, so it is evaluated in a `vm` sandbox with
 * `self` and `importScripts` stubbed — the same globals a worker gets. That makes the one thing this
 * file must never get wrong testable: architecture §2 P4 says a worker that receives a push and does
 * not call `showNotification()` makes the browser display its own message, so **every** input has to
 * end in a notification, including the ones from a future the build has never seen (§6).
 *
 * What this does not cover: the browser actually invoking the handler. That needs a push service.
 */
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(__dirname, "..");

interface Shown {
  title: string;
  options: Record<string, unknown>;
}

let sandbox: Record<string, any>;
let shown: Shown[];
let langInCache: string | null;

function loadWorker() {
  shown = [];
  const listeners: Record<string, (e: unknown) => void> = {};
  const self: Record<string, any> = {
    addEventListener: (name: string, fn: (e: unknown) => void) => {
      listeners[name] = fn;
    },
    registration: {
      showNotification: (title: string, options: Record<string, unknown>) => {
        shown.push({ title, options });
        return Promise.resolve();
      },
    },
    clients: { matchAll: async () => [], openWindow: async () => null, claim: async () => {} },
    skipWaiting: async () => {},
    registration_subscribeCalls: [] as unknown[],
  };
  self.registration.pushManager = {
    subscribe: async (options: Record<string, unknown>) => {
      self.registration_subscribeCalls.push(options);
      return {
        endpoint: "https://push.example/rotated",
        toJSON: () => ({ endpoint: "https://push.example/rotated", keys: { p256dh: "P", auth: "A" } }),
      };
    },
  };
  const context: Record<string, any> = {
    self,
    listeners,
    caches: {
      open: async () => ({
        match: async () =>
          langInCache === null ? undefined : { text: async () => langInCache as string },
      }),
    },
    fetch: async (url: string, init?: Record<string, unknown>) => {
      context.posted.push({ url, init });
      return String(url).includes("/api/push/config")
        ? { ok: true, json: async () => context.pushConfig }
        : { ok: true };
    },
    posted: [] as Record<string, unknown>[],
    pushConfig: { enabled: true, publicKey: `B${"x".repeat(86)}` },
    // Faithful to the BROWSER's `atob`, which is strict: it throws `InvalidCharacterError` on any
    // character outside the standard base64 alphabet. Node's `Buffer.from(s, "base64")` is lenient
    // and silently accepts base64url — so a stub built on it alone would decode `-` and `_` happily
    // and hide a missing conversion, which is exactly what it did until a mutation run caught it.
    atob: (s: string) => {
      if (!/^[A-Za-z0-9+/]*={0,2}$/.test(s)) throw new Error("InvalidCharacterError");
      return Buffer.from(s, "base64").toString("binary");
    },
    Uint8Array,
    Buffer,
    console,
    setTimeout,
    Date,
    Promise,
    Object,
    String,
    JSON,
  };
  context.globalThis = context;
  context.importScripts = (url: string) => {
    const file = path.join(WEB, "public", url.replace(/^\//, ""));
    vm.runInContext(fs.readFileSync(file, "utf8"), context, { filename: file });
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(WEB, "public", "sw.js"), "utf8"), context, {
    filename: "sw.js",
  });
  return context;
}

/** Drive the real `push` handler with a payload and return what it showed. */
async function push(data: unknown): Promise<Shown> {
  const waits: Promise<unknown>[] = [];
  const event = {
    data: data === undefined ? null : { json: () => data },
    waitUntil: (p: Promise<unknown>) => waits.push(p),
  };
  sandbox.listeners.push(event);
  await Promise.all(waits);
  assert.equal(shown.length, 1, "exactly one notification per push, always");
  return shown[0];
}


/** Drive the real `pushsubscriptionchange` handler and return what was POSTed, if anything. */
async function rotate(event: Record<string, unknown>): Promise<Record<string, unknown> | null> {
  const waits: Promise<unknown>[] = [];
  sandbox.listeners.pushsubscriptionchange({
    ...event,
    waitUntil: (p: Promise<unknown>) => waits.push(p),
  });
  await Promise.all(waits);
  const post = sandbox.posted.find((r: Record<string, any>) => r.init?.method === "POST");
  return post ? JSON.parse(post.init.body) : null;
}

const BREAKING = {
  v: 1,
  notificationId: 7,
  kind: "breaking_story",
  payload: { storyId: "st_9", title: "Court issues ruling", publisherCount: 5 },
  dedupeKey: "ev:42",
  lang: "en",
  createdAt: "2026-07-30T11:00:00+00:00",
  sentAt: "2026-07-30T12:00:00+00:00",
  href: "/stories/st_9",
};

before(() => {
  // The worker reads sw-data.js, which the build generates. Fail loudly rather than skip: a missing
  // artifact means the build step is not wired, which is exactly the regression worth catching.
  assert.ok(
    fs.existsSync(path.join(WEB, "public", "sw-data.js")),
    "run `node scripts/build-sw-data.mjs` first — the worker's data is a build artifact",
  );
});

test("the worker's data builds on a Node without TypeScript type stripping", () => {
  /**
   * The production web image is `node:20-slim`. Type stripping arrived in Node 22.18, so a build
   * script that imports a `.ts` file directly works here and fails there with
   * `ERR_UNKNOWN_FILE_EXTENSION` — taking `npm run build`, and therefore the whole image, with it.
   *
   * That shipped once: development runs Node 22 and so does CI, so nothing between the editor and
   * the registry ever ran the Node the container actually uses. `--no-experimental-strip-types` is
   * the closest available simulation, and it is exact for this failure mode.
   */
  const result = spawnSync(
    process.execPath,
    ["--no-experimental-strip-types", "scripts/build-sw-data.mjs"],
    { cwd: WEB, encoding: "utf8" },
  );
  assert.equal(
    result.status,
    0,
    `build-sw-data must not depend on type stripping:\n${result.stderr}`,
  );
});

test("a known kind renders its localized title and body from the payload", async () => {
  langInCache = null;
  sandbox = loadWorker();
  const { title, options } = await push(BREAKING);
  assert.equal(title, "Breaking news");
  assert.equal(options.body, "Court issues ruling", "the body key interpolates the payload");
});

test("the stored language wins over the payload's", async () => {
  // §4: the payload's language was captured at SEND time and a push can sit under its TTL for hours.
  langInCache = "es";
  sandbox = loadWorker();
  const { title } = await push({ ...BREAKING, lang: "en" });
  assert.equal(title, "Última hora");
});

test("the payload's language is used when the device has none stored", async () => {
  langInCache = null;
  sandbox = loadWorker();
  assert.equal((await push({ ...BREAKING, lang: "de" })).title, "Eilmeldung");
});

test("an unsupported language anywhere falls through to English", async () => {
  langInCache = "kl";
  sandbox = loadWorker();
  assert.equal((await push({ ...BREAKING, lang: "zz" })).title, "Breaking news");
});

test("an unsupported stored language does not shadow the payload's supported one", async () => {
  // The test above passes even without the supported-set check, because a catalog miss lands on
  // English anyway — the right answer for the wrong reason. Here the two differ: a junk stored value
  // must be rejected outright so the payload's language is what renders.
  langInCache = "kl";
  sandbox = loadWorker();
  assert.equal((await push({ ...BREAKING, lang: "es" })).title, "Última hora");
});

test("a storage failure falls back rather than losing the notification", async () => {
  // Private mode and quota exhaustion both make the Cache API throw. §2 P4: an exception on the
  // render path is the browser's own "site updated in the background" message.
  sandbox = loadWorker();
  sandbox.caches.open = () => {
    throw new Error("quota exceeded");
  };
  assert.equal((await push({ ...BREAKING, lang: "de" })).title, "Eilmeldung");
});

test("a placeholder the payload cannot fill stays visible instead of blanking", async () => {
  // Mirrors `lib/i18n-core.ts`. A visible `{missing}` is a bug report from a reader; a silent blank
  // is a mystery, and the notification still has to say something.
  langInCache = null;
  sandbox = loadWorker();
  sandbox.self.IH_MESSAGES.en["notifications.breaking_story.body"] = "{title} — {missing}";
  assert.equal((await push(BREAKING)).options.body, "Court issues ruling — {missing}");
});

test("a whitespace-only deep-link id falls back to the static page", async () => {
  langInCache = null;
  sandbox = loadWorker();
  const { options } = await push({ ...BREAKING, payload: { storyId: "   ", title: "x" } });
  assert.equal((options.data as { href: string }).href, "/stories", "never `/stories/`");
});

test("a story id is escaped, because a payload is data and not a path", async () => {
  langInCache = null;
  sandbox = loadWorker();
  const { options } = await push({ ...BREAKING, payload: { storyId: "a/b?c#d", title: "x" } });
  assert.equal((options.data as { href: string }).href, "/stories/a%2Fb%3Fc%23d");
});

// --------------------------------------------------------------------------------------------- //
// A STALE CATALOG. `sw-data.js` is a cached build artifact, so a device can hold last week's copy
// while the server sends this week's kind — the §6 case the build's own key check cannot prevent,
// because the build only ever sees one version at a time. Injected into the sandbox for that reason.
// --------------------------------------------------------------------------------------------- //
test("a key the device has never seen renders app copy, never the key itself", async () => {
  langInCache = null;
  sandbox = loadWorker();
  sandbox.self.IH_KINDS.a_newer_kind = {
    titleKey: "notifications.a_newer_kind.title",
    bodyKey: "notifications.a_newer_kind.body",
    href: "/newer",
    deepLinkField: null,
    deepLinkPath: null,
  };
  const { title, options } = await push({ ...BREAKING, kind: "a_newer_kind" });
  assert.ok(!title.includes("notifications."), "a raw i18n key on a lock screen is the worst outcome");
  assert.equal(title, "Hidden View", "app copy, so the notification still says something");
  assert.equal(options.body, undefined, "absent means render without it, not render the key");
  assert.equal((options.data as { href: string }).href, "/newer");
});

test("a key missing from the reader's language falls back to English, not to nothing", async () => {
  langInCache = "es";
  sandbox = loadWorker();
  sandbox.self.IH_KINDS.partly_translated = {
    titleKey: "notifications.partly_translated.title",
    bodyKey: null,
    href: "/x",
    deepLinkField: null,
    deepLinkPath: null,
  };
  sandbox.self.IH_MESSAGES.en["notifications.partly_translated.title"] = "Only in English";
  const { title } = await push({ ...BREAKING, kind: "partly_translated" });
  assert.equal(title, "Only in English");
});

test("a known kind derives its own deep link rather than trusting the payload's", async () => {
  // §2's precedence rule: the payload is a fallback for what the device cannot derive.
  langInCache = null;
  sandbox = loadWorker();
  const { options } = await push({ ...BREAKING, href: "/stale-from-an-older-server" });
  assert.equal((options.data as { href: string }).href, "/stories/st_9");
});

test("the dedupe key becomes the tag, so a repeat collapses instead of stacking", async () => {
  langInCache = null;
  sandbox = loadWorker();
  assert.equal((await push(BREAKING)).options.tag, "ev:42");
});

// --------------------------------------------------------------------------------------------- //
// §6 — forward compatibility. This worker is what an OLD device runs when a NEW server sends.
// --------------------------------------------------------------------------------------------- //
test("an unknown kind still renders, and still navigates via the server's href", async () => {
  langInCache = null;
  sandbox = loadWorker();
  const { title, options } = await push({
    ...BREAKING,
    kind: "a_kind_from_a_later_release",
    href: "/some/new/page",
  });
  assert.equal(title, "Notification", "generic app-level copy, never a raw key");
  assert.equal((options.data as { href: string }).href, "/some/new/page");
});

test("a higher schema version renders rather than refusing", async () => {
  langInCache = null;
  sandbox = loadWorker();
  const { title } = await push({ ...BREAKING, v: 99, kind: "future_kind" });
  assert.equal(title, "Notification");
});

test("unknown extra fields are ignored, not fatal", async () => {
  langInCache = null;
  sandbox = loadWorker();
  const { title } = await push({ ...BREAKING, somethingNew: { nested: true }, alsoNew: 5 });
  assert.equal(title, "Breaking news");
});

test("a payload that will not parse still produces a notification", async () => {
  langInCache = null;
  sandbox = loadWorker();
  const waits: Promise<unknown>[] = [];
  sandbox.listeners.push({
    data: {
      json: () => {
        throw new SyntaxError("not json");
      },
    },
    waitUntil: (p: Promise<unknown>) => waits.push(p),
  });
  await Promise.all(waits);
  assert.equal(shown.length, 1);
  assert.equal(shown[0].title, "Notification");
});

test("a push with no data at all still produces a notification", async () => {
  langInCache = null;
  sandbox = loadWorker();
  const { title, options } = await push(undefined);
  assert.equal(title, "Notification");
  assert.equal((options.data as { href: string }).href, "/", "tappable even with nothing to go on");
});

test("a known kind with a broken payload falls back to its static page, not a broken URL", async () => {
  langInCache = null;
  sandbox = loadWorker();
  const { options } = await push({ ...BREAKING, payload: { title: "x" }, href: "/ignored" });
  assert.equal((options.data as { href: string }).href, "/stories");
});

test("no fetch happens on the render path", async () => {
  // §2 P4. A network call before showNotification() makes the browser's generic message the outcome
  // whenever connectivity is poor — which is exactly when a queued push tends to arrive.
  langInCache = null;
  sandbox = loadWorker();
  let fetched = 0;
  sandbox.fetch = () => {
    fetched += 1;
    return Promise.resolve({ ok: true });
  };
  await push(BREAKING);
  assert.equal(fetched, 0);
});

// --------------------------------------------------------------------------------------------- //
// `pushsubscriptionchange` — the browser rotating this device's subscription behind our back.
//
// Not the render path, so a network call is allowed here (§2 P4 constrains the push handler only).
// The reason it is allowed is the whole point of these tests: without a network fallback, a browser
// that fires this event with neither a new subscription nor the old options leaves the device
// unsubscribed and the engine never told — silent, and indistinguishable from a reader who turned
// push off.
// --------------------------------------------------------------------------------------------- //
test("a rotation with a new subscription registers it without asking the server", async () => {
  sandbox = loadWorker();
  const body = await rotate({
    newSubscription: {
      endpoint: "https://push.example/fresh",
      toJSON: () => ({ endpoint: "https://push.example/fresh", keys: { p256dh: "P", auth: "A" } }),
    },
  });
  assert.equal(body?.endpoint, "https://push.example/fresh");
  assert.equal(body?.reason, "worker", "attributable in the log, not indistinguishable from a reader");
  assert.equal(sandbox.self.registration_subscribeCalls.length, 0, "nothing to re-subscribe");
});

test("a rotation without a new subscription re-subscribes from the old options", async () => {
  sandbox = loadWorker();
  const key = new Uint8Array([4, 1, 2, 3]);
  const body = await rotate({ oldSubscription: { options: { applicationServerKey: key } } });
  assert.equal(sandbox.self.registration_subscribeCalls[0].applicationServerKey, key);
  assert.equal(body?.endpoint, "https://push.example/rotated");
});

test("a rotation with NOTHING to go on falls back to the server's key", async () => {
  // The gap: the spec permits an event carrying neither, and implementations differ. Before this
  // fallback the handler returned silently — the old endpoint kept failing until a `410` pruned it,
  // and the reader stopped receiving anything while their toggle still read "on".
  sandbox = loadWorker();
  const body = await rotate({});
  assert.equal(sandbox.self.registration_subscribeCalls.length, 1, "it re-subscribed");
  assert.ok(
    sandbox.posted.some((r: Record<string, any>) => String(r.url).includes("/api/push/config")),
    "and it asked the server for the key to do it with",
  );
  assert.equal(body?.endpoint, "https://push.example/rotated");
});

test("a rotation decodes a real base64url key, not a base64 one", async () => {
  // A VAPID key routinely contains `-` and `_`. Handed to `atob` unconverted it throws, the fallback
  // returns null, and the device is lost in exactly the case this fallback exists for — a failure
  // that every key WITHOUT those two characters hides.
  sandbox = loadWorker();
  sandbox.pushConfig = {
    enabled: true,
    publicKey:
      "BL1RENubg-oBKgqFaC9dBBqqmfnp1uJ_xl4o1D-WRUEoyTIVt_rOhCFQ0DM80BRTkoasGfN0gql_l9jCzL0J29U",
  };
  await rotate({});
  const key = sandbox.self.registration_subscribeCalls[0]?.applicationServerKey;
  assert.ok(key, "it re-subscribed rather than throwing on the key");
  assert.deepEqual([...key.slice(0, 4)], [4, 189, 81, 16], "decoded as base64url, byte for byte");
  assert.equal(key.length, 65, "an uncompressed P-256 point");
});

test("a rotation respects the switch, not just the presence of a key", async () => {
  // The rollback state is `enabled: false` with the key STILL configured — §5 keeps the pair in place
  // so push can be switched back on without regenerating it. Reading only the key would re-subscribe
  // devices against a deployment that has been told to stop sending.
  sandbox = loadWorker();
  sandbox.pushConfig = { enabled: false, publicKey: `B${"x".repeat(86)}` };
  assert.equal(await rotate({}), null);
  assert.equal(sandbox.self.registration_subscribeCalls.length, 0);
});

test("a rotation gives up quietly when the deployment no longer offers push", async () => {
  // Rolled back server-side: there is no key to subscribe against, and inventing one would produce a
  // subscription nothing can ever sign for. Doing nothing is correct here — unlike the case above,
  // it is not a silent loss, because there is nothing to lose.
  sandbox = loadWorker();
  sandbox.pushConfig = { enabled: false, publicKey: "" };
  assert.equal(await rotate({}), null);
  assert.equal(sandbox.self.registration_subscribeCalls.length, 0);
});

test("a rotation survives an unreachable server", async () => {
  // It runs inside `waitUntil`; an exception escaping would be an unhandled rejection in the worker.
  sandbox = loadWorker();
  sandbox.fetch = async () => {
    throw new Error("offline");
  };
  await rotate({});                       // must not reject
  assert.equal(sandbox.self.registration_subscribeCalls.length, 0);
});
