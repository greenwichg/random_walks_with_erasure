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
    // platform, so `PushToggle` renders nothing in the `unavailable` state. This browser holds no
    // subscription, which is what distinguishes it from `paused` below.
    await authedPage.goto("/settings");
    await expect(authedPage.getByText("Breaking news").first()).toBeVisible();
    await expect(authedPage.getByText("Push notifications on this device")).toHaveCount(0);
  });

  test("the breaking-news PUSH preference is absent when the deployment cannot send", async ({
    authedPage,
  }) => {
    // Same reasoning as the per-device control above, applied to the account-level preference: a
    // switch for a channel with no sender behind it is a promise we don't keep. The in-app switch for
    // the same category stays, because that channel does deliver.
    await authedPage.goto("/settings");
    await expect(authedPage.getByRole("switch", { name: "Breaking news" })).toBeVisible();
    await expect(
      authedPage.getByRole("switch", { name: "Breaking news on your devices" }),
    ).toHaveCount(0);
  });

  test("the breaking-news PUSH preference appears and saves once push is configured", async ({
    authedPage,
  }) => {
    // The gap this test exists for: B2 shipped a sender and nothing in the UI could turn the push
    // channel on, so every reader who registered a device received exactly nothing. Found by walking
    // the pipeline end to end on production, not by any test — hence this one.
    //
    // The config route is stubbed rather than the deployment reconfigured: what is under test is the
    // UI's response to an install that CAN send, and minting a real subscription needs a live push
    // service this suite does not have.
    //
    // Since R1b the config usually arrives inside `/api/bootstrap` (whose handler asks the engine
    // server-side, out of `page.route`'s reach), so the bootstrap is stubbed too: its pushConfig
    // section carries the same capable config, and the null sections make every other shell query
    // fall back to its real endpoint. The direct stub stays for the fallback path.
    const capable = { enabled: true, publicKey: `B${"x".repeat(86)}` };
    await authedPage.route("**/api/bootstrap", (route) =>
      route.fulfill({
        json: { dashboard: null, settings: null, notifications: null, pushConfig: capable },
      }),
    );
    await authedPage.route("**/api/push/config", (route) => route.fulfill({ json: capable }));
    await authedPage.goto("/settings");

    const pushPref = authedPage.getByRole("switch", { name: "Breaking news on your devices" });
    await expect(pushPref).toBeVisible();
    await expect(pushPref).toHaveAttribute("data-state", "unchecked"); // off by default — consent

    const [response] = await Promise.all([
      authedPage.waitForResponse(
        (r) => r.url().includes("/api/settings") && r.request().method() === "POST",
      ),
      (async () => {
        await pushPref.click();
        await authedPage.getByRole("button", { name: "Save changes" }).click();
      })(),
    ]);

    // The preference reached the server on the right leaf of the category x channel matrix, and the
    // in-app sibling was not restated — a save must carry the leaf that changed and nothing else.
    const sent = JSON.parse(response.request().postData() ?? "{}");
    expect(sent.notifications.categories.breaking).toEqual({ push: true });

    await authedPage.reload();
    await expect(
      authedPage.getByRole("switch", { name: "Breaking news on your devices" }),
    ).toHaveAttribute("data-state", "checked");
  });

  test("reads and deletes stay open while registration is closed (P4)", async ({ authedPage }) => {
    // The suite runs with push UNCONFIGURED, which is the rolled-back state. Registration must be
    // refused and the other two must not be: a row survives a rollback by design, so the way out has
    // to keep working while the way in is shut. Asserted at the API, because the UI half depends on
    // this browser holding a subscription — which needs a live push service.
    await authedPage.goto("/settings");

    const listed = await authedPage.request.get("/api/push/subscriptions");
    expect(listed.status(), "a reader can always see what is registered for them").toBe(200);
    expect(await listed.json()).toEqual([]);

    const removed = await authedPage.request.delete(
      "/api/push/subscriptions?endpoint=" + encodeURIComponent("https://fcm.example/none"),
    );
    expect(removed.status(), "and can always remove one").toBe(200);
    expect((await removed.json()).removed).toBe(false);

    const registered = await authedPage.request.post("/api/push/subscriptions", {
      data: { endpoint: "https://fcm.example/x", p256dh: "BKeyAAAA", auth: "AuthAAAA" },
    });
    expect(registered.status(), "but no NEW device may register").toBe(503);
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

  /** Poll the language store the service worker reads (§4). */
  async function storedLanguage(page: import("@playwright/test").Page) {
    return page.evaluate(async () => {
      for (let i = 0; i < 40; i += 1) {
        const cache = await caches.open("ih-prefs-v1");
        const hit = await cache.match("/__ih/lang");
        if (hit) return (await hit.text()).trim();
        await new Promise((r) => setTimeout(r, 100));
      }
      return null;
    });
  }

  test("the reader's language is published from ANY page, not just Settings", async ({
    authedPage,
  }) => {
    // §4: the worker resolves the language from browser storage FIRST, because the payload's copy was
    // captured at send time and can be stale by the time a push renders. This is the write half of
    // that contract.
    //
    // REGRESSION (P1): this used to live in the push hook, which is mounted only by the Settings
    // control — so a reader who never opened Settings had nothing on file and every push would have
    // rendered in the fallback language. The assertion is deliberately made from the dashboard, which
    // has no push UI at all: publication belongs to the language lifecycle, not to a page.
    await authedPage.goto("/");
    expect(await storedLanguage(authedPage)).toBe("en");
  });

  test("the published language follows the reader's setting", async ({ authedPage }) => {
    // The provider publishes on every change, so a switch reaches the worker's store without the
    // reader visiting anything push-related afterwards.
    await authedPage.goto("/settings");
    expect(await storedLanguage(authedPage)).toBe("en");

    await authedPage.request.post("/api/settings", { data: { language: "es" } });
    await authedPage.goto("/");
    await expect.poll(() => storedLanguage(authedPage)).toBe("es");
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
