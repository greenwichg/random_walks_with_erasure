/**
 * Shared Playwright fixtures. `test` extends the base runner with:
 *
 *   - `uid`         a fresh, isolated engine user for this test (per-test state isolation → determinism)
 *   - `authedPage`  a Page already signed in as `uid` (session cookie pre-seeded, its own context)
 *
 * Specs import `{ test, expect }` from here instead of `@playwright/test`. The auth spec deliberately
 * does NOT use `authedPage` — it drives the real sign-in flow itself.
 */
import { test as base, expect, type Page } from "@playwright/test";
import { createEngineUser, mintSessionCookie } from "./helpers";

interface Fixtures {
  uid: number;
  authedPage: Page;
}

export const test = base.extend<Fixtures>({
  uid: async ({}, use) => {
    await use(await createEngineUser());
  },

  authedPage: async ({ browser, uid }, use) => {
    const context = await browser.newContext();
    await context.addCookies([await mintSessionCookie(uid)]);
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

export { expect };
