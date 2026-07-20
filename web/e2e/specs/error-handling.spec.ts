import { test, expect } from "../fixtures";

/**
 * Journey 7 — Error Handling. The web server runs with the PRODUCTION mock policy
 * (RWE_ALLOW_MOCK_FALLBACK=false), so mock data can never be served. This spec verifies:
 *   - a protected endpoint rejects an anonymous request with 401 (never demo/mock data),
 *   - an unavailable data source surfaces the app's error state, with no fabricated content beside it.
 */
test.describe("Error Handling", () => {
  test("anonymous requests to protected endpoints return 401", async ({ request }) => {
    // The base `request` fixture carries no session cookie.
    for (const path of ["/api/history", "/api/me/saved", "/api/settings"]) {
      expect(await request.get(path).then((r) => r.status()), path).toBe(401);
    }
  });

  test("an unavailable backend shows the error state, not fabricated data", async ({ authedPage }) => {
    const page = authedPage;
    // Simulate the data source being unavailable (503) — the production posture. The app must show
    // its error state and NOT fall back to mock numbers.
    await page.route("**/api/dashboard", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "engine_unavailable", message: "unavailable" } }),
      }),
    );

    await page.goto("/");
    // Graceful error state is shown...
    await expect(page.getByText("Something went wrong")).toBeVisible();
    // ...and no dashboard content (mock or otherwise) rendered alongside it.
    await expect(page.getByText("Health trend")).toHaveCount(0);
  });
});
