import { test, expect } from "../fixtures";

/**
 * Journey 1 — Authentication. Drives the real NextAuth sign-in (the automatable demo credentials
 * provider, which flows through the same session + engine-upsert path as Google), verifies the
 * session is established, and that a protected page (the Dashboard) then loads.
 *
 * A signed-out visitor to a protected page is also verified to be redirected away by the middleware.
 */
test.describe("Authentication", () => {
  test("a signed-out visitor is redirected off a protected page", async ({ browser }) => {
    const context = await browser.newContext(); // no session cookie
    const page = await context.newPage();
    await page.goto("/settings");
    // middleware sends unauthenticated visitors to the public onboarding funnel (never the app page)
    await expect(page).not.toHaveURL(/\/settings/);
    await context.close();
  });

  test("signing in without onboarding lands on the funnel, not the app", async ({ browser }) => {
    // THE BYPASS THIS GATE CLOSES. `/signin` is the last step of the onboarding funnel — the estimate
    // screen navigates here itself — but it is also reachable directly, from an
    // `?error=AccessDenied` bounce, a bookmark, or a beta invite link. NextAuth then returns the
    // reader to `callbackUrl: "/"`, and before the gate they arrived in the app with no outlets and
    // no reads: the exact state that made every personalised surface fall back to another reader's
    // data. Signing in here reproduces that entry path.
    const context = await browser.newContext(); // start signed out
    const page = await context.newPage();

    await page.goto("/signin");
    await page.getByRole("button", { name: "Continue as demo reader" }).click();

    // The session is genuinely established... (via `/signin/complete`, which passes a reader with
    // nothing stashed straight through)
    await page.waitForURL((url) => url.pathname === "/onboarding" || url.pathname === "/");
    const session = await page.request.get("/api/auth/session").then((r) => r.json());
    expect(session?.user).toBeTruthy();

    // ...and the app shell sent them to the funnel rather than serving a personalised page they
    // have no data for.
    await expect(page).toHaveURL(/\/onboarding/);

    await context.close();
  });

  test("completing the funnel then signing in lands in the app, not back at the funnel", async ({
    browser,
  }) => {
    // The other half of the gate's contract, and the flow with the most traffic. An anonymous
    // visitor's selection is stashed client-side (no account exists yet), so if sign-in returned
    // them straight to `/` the store would still know nothing about them and the gate would send
    // them through the funnel a second time. Sign-in instead returns to `/signin/complete`, which
    // persists the stash first; this test is the reason that step exists.
    const context = await browser.newContext();
    const page = await context.newPage();

    await page.goto("/onboarding");
    await page.getByRole("button", { name: "See a sample first" }).click();   // fastest path to an estimate
    const save = page.getByRole("button", { name: "Save my estimate & track it" });
    await save.waitFor({ timeout: 30_000 });                                  // engine computes the estimate
    await save.click();

    await page.waitForURL(/\/signin/);
    await page.getByRole("button", { name: "Continue as demo reader" }).click();

    await expect(page).not.toHaveURL(/\/onboarding/);
    await expect(
      page.getByRole("main").getByRole("heading", { name: "Today", exact: true }),
    ).toBeVisible();

    // The landing step replaced its own history entry rather than pushing one, so Back cannot return
    // to it. An interstitial reachable by Back is the one way this step could look like a loop.
    await page.goBack();
    await expect(page).not.toHaveURL(/\/signin\/complete/);

    await context.close();
  });

  test("re-entering the sign-in landing step is a no-op for an onboarded reader", async ({
    authedPage,
  }) => {
    // Idempotency, from the direction that actually happens in the wild: a refresh, a duplicated tab,
    // or a bookmarked URL. The step checks `/api/me` before writing, so an established account is
    // passed through untouched — it must not re-post a stale selection over real outlets, and it must
    // not bounce to the funnel.
    await authedPage.goto("/signin/complete");
    await authedPage.waitForURL((url) => url.pathname === "/");
    await expect(
      authedPage.getByRole("main").getByRole("heading", { name: "Today", exact: true }),
    ).toBeVisible();
  });

  test("an onboarded reader goes straight to the dashboard", async ({ authedPage }) => {
    // The other side of the gate: it must not stand between an established reader and the product.
    // `authedPage` is onboarded (see e2e/fixtures.ts), so this is the regression test for the gate
    // over-firing — the failure mode that would be far worse than the bug it fixes.
    await authedPage.goto("/");
    await expect(authedPage).not.toHaveURL(/\/onboarding/);
    await expect(
      authedPage.getByRole("main").getByRole("heading", { name: "Today", exact: true }),
    ).toBeVisible();
  });
});
