import { test, expect } from "../fixtures";

/**
 * Journey 11 — Browser Push Foundation (B1).
 *
 * What a real browser can prove here, and what it cannot. A full subscription needs a live push
 * service to mint an endpoint, which no local suite has — so these tests cover everything up to that
 * boundary: the availability gate the UI reads, the service worker actually registering and
 * activating, and the reader's language reaching the store the worker reads it from (architecture
 * §4). Subscription storage itself is covered against the real engine in
 * `tests/test_push_subscriptions_api.py`, and the pure logic in `lib/push.test.ts`.
 *
 * The suite runs with push UNCONFIGURED (no VAPID key in `playwright.config.ts`), which is also the
 * production default — so the first test pins the fail-closed path that every reader currently sees.
 */
test.describe("Browser push foundation", () => {
  test("push reports unavailable when the deployment has no VAPID key", async ({ authedPage }) => {
    // Fail-closed (§ the config route): an engine with no key must report the feature off rather than
    // let the UI offer a permission prompt that cannot be honoured.
    await authedPage.goto("/settings");
    const config = await authedPage.request.get("/api/push/config").then((r) => r.json());
    expect(config).toEqual({ enabled: false, publicKey: "" });
  });

  test("the per-device control is absent — not disabled — when push is unavailable", async ({
    authedPage,
  }) => {
    // A permanently greyed switch reads as a fault in the product rather than a fact about the
    // platform, so `PushToggle` renders nothing in the `unavailable` state.
    await authedPage.goto("/settings");
    await expect(authedPage.getByText("Breaking news").first()).toBeVisible();
    await expect(authedPage.getByText("Push notifications on this device")).toHaveCount(0);
  });

  test("the service worker registers and activates", async ({ authedPage }) => {
    // Registration happens regardless of whether a reader ever enables push: it prompts for nothing,
    // and it is what lets `pushsubscriptionchange` reach us on a device that granted permission on an
    // earlier visit.
    await authedPage.goto("/settings");
    const scriptURL = await authedPage.evaluate(async () => {
      const reg = await navigator.serviceWorker.register("/sw.js");
      const ready = await navigator.serviceWorker.ready;
      return ready.active?.scriptURL ?? reg.active?.scriptURL ?? null;
    });
    expect(scriptURL).toContain("/sw.js");
  });

  test("the worker controls the whole origin, not just its own directory", async ({ authedPage }) => {
    // Served from /sw.js precisely so its scope is "/" — a worker scoped to a subdirectory would not
    // receive pushes for the app's pages.
    await authedPage.goto("/settings");
    const scope = await authedPage.evaluate(async () => {
      await navigator.serviceWorker.register("/sw.js");
      return (await navigator.serviceWorker.ready).scope;
    });
    expect(new URL(scope).pathname).toBe("/");
  });

  test("the reader's language is published where the worker can read it", async ({ authedPage }) => {
    // §4: the worker resolves the language from browser storage FIRST, because the payload's copy was
    // captured at send time and can be stale by the time a push renders. This is the write half of
    // that contract — without it the worker only ever sees the fallback.
    await authedPage.goto("/settings");
    const stored = await authedPage.evaluate(async () => {
      for (let i = 0; i < 40; i += 1) {
        const cache = await caches.open("ih-prefs-v1");
        const hit = await cache.match("/__ih/lang");
        if (hit) return (await hit.text()).trim();
        await new Promise((r) => setTimeout(r, 100));
      }
      return null;
    });
    expect(stored).toBe("en");
  });

  test("a push handler exists, so a delivered push can never become the browser's generic message", async ({
    authedPage,
  }) => {
    // Architecture §2 P4 / §6: a worker that receives a push and does not call showNotification()
    // makes the browser show "This site has been updated in the background". B1 ships the generic
    // floor for exactly that reason — a device holding a subscription from this deploy must render
    // correctly even when a later deploy sends a kind it has never heard of.
    const source = await authedPage.request.get("/sw.js").then((r) => r.text());
    expect(source).toContain('addEventListener("push"');
    expect(source).toContain("showNotification");
    expect(source).toContain('addEventListener("pushsubscriptionchange"');
  });
});
