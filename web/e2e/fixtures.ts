/**
 * Shared Playwright fixtures. `test` extends the base runner with:
 *
 *   - `uid`         a fresh, isolated engine user for this test (per-test state isolation → determinism)
 *   - `authedPage`  a Page already signed in as `uid`, ONBOARDED (session cookie pre-seeded, its own
 *                   context)
 *
 * Specs import `{ test, expect }` from here instead of `@playwright/test`. The auth spec deliberately
 * does NOT use `authedPage` — it drives the real sign-in flow itself, which is now the only place
 * the onboarding gate is exercised.
 *
 * WHY `authedPage` ONBOARDS: the app shell redirects a signed-in reader with no onboarding and no
 * reads to `/onboarding`, so an un-onboarded fixture would bounce every feature spec off its own
 * page. Seeding here makes explicit what all nine specs already assumed — they test an ESTABLISHED
 * reader's surfaces (settings, analytics, publishers, saved), not the first-run funnel. Only
 * health-report.spec seeded it before, and only to dodge the demo-report fallback; that same
 * fallback is what the gate now prevents at the source.
 */
import { test as base, expect, type Page } from "@playwright/test";
import { createEngineUser, mintSessionCookie, seedOnboarding } from "./helpers";

interface Fixtures {
  uid: number;
  authedPage: Page;
}

export const test = base.extend<Fixtures>({
  uid: async ({}, use) => {
    await use(await createEngineUser());
  },

  authedPage: async ({ browser, uid }, use) => {
    await seedOnboarding(uid);          // an established reader, not a first-run one — see above
    const context = await browser.newContext();
    await context.addCookies([await mintSessionCookie(uid)]);
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

export { expect };
