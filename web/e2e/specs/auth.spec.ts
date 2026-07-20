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

  test("sign in establishes a session and loads the dashboard", async ({ browser }) => {
    const context = await browser.newContext(); // start signed out
    const page = await context.newPage();

    await page.goto("/signin");
    await page.getByRole("button", { name: "Continue as demo reader" }).click();

    // On success the page navigates itself to "/" (the dashboard).
    await page.waitForURL((url) => url.pathname === "/");
    // The header also renders the page title as an h1, so scope to the main content.
    await expect(page.getByRole("main").getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();

    // The session is genuinely established (an authenticated user is present).
    const session = await page.request.get("/api/auth/session").then((r) => r.json());
    expect(session?.user).toBeTruthy();

    await context.close();
  });
});
