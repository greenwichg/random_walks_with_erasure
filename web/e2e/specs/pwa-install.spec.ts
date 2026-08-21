import { test, expect } from "../fixtures";

/**
 * Installability, asked of the browser rather than inferred from a checklist.
 *
 * Chromium's criteria have moved across versions — the offline-response requirement in particular
 * has been added, relaxed and re-worded — so a test that asserted "we have a manifest and a fetch
 * handler, therefore it installs" would be asserting my reading of a changelog. `Page.getAppManifest`
 * and `Page.getInstallabilityErrors` are the browser's own answer, and they are what this checks.
 *
 * The other half is the safety property: the worker must be invisible to `/api/*`. That one cannot
 * be tested with Playwright's `setOffline` — it does not reach service-worker fetches, so the
 * worker's own `fetch()` still succeeds and an "offline" test silently proves nothing. The offline
 * path is covered against a real outage in `lib/sw-fetch-policy.test.ts` plus the manual procedure
 * in docs/PWA_OPERATIONS.md.
 */
test.describe("PWA", () => {
  test("Chromium reports no installability errors", async ({ authedPage }) => {
    await authedPage.goto("/", { waitUntil: "networkidle" });

    // The registrar runs on `load`; give the worker a moment to reach `activated`.
    await authedPage.waitForFunction(
      async () => !!(await navigator.serviceWorker.getRegistration())?.active,
      undefined,
      { timeout: 15_000 },
    );

    const cdp = await authedPage.context().newCDPSession(authedPage);
    await cdp.send("Page.enable");

    const manifest = (await cdp.send("Page.getAppManifest")) as { errors?: unknown[]; url?: string };
    expect(manifest.url, "a manifest must be linked from the document").toContain("site.webmanifest");
    expect(manifest.errors ?? [], "the manifest must parse without warnings").toEqual([]);

    const { installabilityErrors } = (await cdp.send("Page.getInstallabilityErrors")) as {
      installabilityErrors: { errorId: string }[];
    };
    expect(
      installabilityErrors,
      `Chromium refused installation: ${JSON.stringify(installabilityErrors)}`,
    ).toEqual([]);
  });

  test("the service worker registers with push disabled", async ({ authedPage }) => {
    // The regression this guards: registration used to live ONLY inside PushReconciler, behind
    // RWE_PUSH_ENABLED, and production runs with it 0 — so no worker existed and the app could not
    // be installed, for a reason that had nothing to do with installation.
    await authedPage.goto("/", { waitUntil: "networkidle" });
    const scope = await authedPage.evaluate(async () => {
      for (let i = 0; i < 60; i++) {
        const r = await navigator.serviceWorker.getRegistration();
        if (r?.active) return r.scope;
        await new Promise((res) => setTimeout(res, 250));
      }
      return null;
    });
    expect(scope, "no worker registered — the app is not installable").not.toBeNull();
    expect(scope).toMatch(/\/$/);
  });

  test("the worker precaches the offline shell and nothing personal", async ({ authedPage }) => {
    await authedPage.goto("/", { waitUntil: "networkidle" });
    await authedPage.waitForFunction(
      async () => !!(await navigator.serviceWorker.getRegistration())?.active,
      undefined,
      { timeout: 15_000 },
    );
    const entries = await authedPage.evaluate(async () => {
      const out: string[] = [];
      for (const name of await caches.keys()) {
        if (!name.startsWith("ih-shell-")) continue;
        const c = await caches.open(name);
        out.push(...(await c.keys()).map((r) => new URL(r.url).pathname));
      }
      return out.sort();
    });
    // Exactly the shell. A route that rendered a reader's data would be frozen in their browser
    // until the next deploy; an /api/* entry would be one reader's data in a shared cache.
    expect(entries).toEqual(["/icon.svg", "/offline", "/site.webmanifest"]);
    expect(entries.some((p) => p.startsWith("/api"))).toBe(false);
  });

  test("a shell activation never deletes push's cache", async ({ authedPage }) => {
    // `ih-prefs-v1` belongs to push and is deliberately outside the shell's `ih-shell-` cleanup
    // prefix. Deleting it would silently reset every reader's notification language to the default.
    //
    // Asserted on a SENTINEL key rather than on `/__ih/lang`: the app syncs the reader's real
    // language into that key on load, so a value written here is legitimately overwritten and
    // testing it would be testing the app's own behaviour, not the cache's survival. (Measured:
    // writing "fr" and reloading yields "en", which is correct and is not what this is about.)
    await authedPage.goto("/", { waitUntil: "networkidle" });
    await authedPage.evaluate(async () => {
      const c = await caches.open("ih-prefs-v1");
      await c.put("/__ih/pwa-test-sentinel", new Response("keep-me"));
    });
    await authedPage.reload({ waitUntil: "networkidle" });
    await authedPage.waitForFunction(
      async () => !!(await navigator.serviceWorker.getRegistration())?.active,
      undefined,
      { timeout: 15_000 },
    );
    const state = await authedPage.evaluate(async () => {
      const names = await caches.keys();
      const hit = await caches.match("/__ih/pwa-test-sentinel");
      return { names, sentinel: hit ? (await hit.text()).trim() : null };
    });
    expect(state.names, "push's cache was evicted by the shell").toContain("ih-prefs-v1");
    expect(state.sentinel, "push's cache was emptied by the shell").toBe("keep-me");
  });
});
