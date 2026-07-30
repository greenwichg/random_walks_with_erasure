import { test, before } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
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
    fetch: async () => ({ ok: true }),
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
