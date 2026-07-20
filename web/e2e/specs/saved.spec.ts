import { test, expect } from "../fixtures";

/**
 * Journey 6 — Saved Articles. Save a recommendation, confirm it persists on the Saved page across a
 * reload, then unsave it back to the empty state. Each write waits for its persist call before the
 * next navigation, so the assertions never race the optimistic UI.
 */
test.describe("Saved Articles", () => {
  test("save persists across a reload, then unsave clears it", async ({ authedPage }) => {
    const page = authedPage;
    await page.goto("/recommendations");
    const firstCard = page.locator("article").first();
    await expect(firstCard).toBeVisible();
    const headline = (await firstCard.locator("h3, h2, [class*='font-semibold']").first().innerText()).trim();

    // Save it — wait for the persist so the Saved page can load it.
    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/me/saved") && r.request().method() === "POST"),
      firstCard.getByRole("button", { name: "Save", exact: true }).click(),
    ]);
    await expect(firstCard.getByRole("button", { name: "Saved", exact: true })).toBeVisible();

    // Persisted: it appears on the Saved page and survives a reload.
    await page.goto("/saved");
    await expect(page.getByText(headline, { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText(headline, { exact: true })).toBeVisible();

    // Unsave (wait for the delete to persist), then the empty state after a reload.
    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/me/saved") && r.request().method() === "DELETE"),
      page.getByRole("button", { name: "Saved", exact: true }).click(),
    ]);
    await expect(page.getByText(headline, { exact: true })).toHaveCount(0);
    await page.reload();
    await expect(page.getByText("No saved articles yet")).toBeVisible();
  });
});
