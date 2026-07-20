import { test, expect } from "../fixtures";

/**
 * Journey 2 — Reading History. A fresh reader has empty history; recording a read through the
 * canonical `/api/me/reads` pipeline (the exact endpoint the in-app Read button's beacon calls, here
 * driven from the authenticated browser context) then shows up in History and moves the Dashboard's
 * "today" metrics. Synthetic corpus articles carry no external URL, so the read is submitted through
 * that endpoint rather than by clicking a (disabled) card link — same pipeline, same persistence.
 */
test.describe("Reading History", () => {
  test("recording a read updates History and the Dashboard", async ({ authedPage, uid }) => {
    const page = authedPage;
    const title = `E2E history read ${uid}`;

    // 1) A brand-new reader has no history: the seeded title is absent.
    await page.goto("/history");
    await expect(page.getByText(title)).toHaveCount(0);

    // 2) Record the read through the real pipeline (browser context → Next proxy → engine).
    const res = await page.request.post("/api/me/reads", {
      data: { reads: [{ url: `https://e2e.example/history/${uid}`, title }] },
    });
    expect(res.ok()).toBeTruthy();

    // 3) History reflects it after a refetch.
    await page.goto("/history");
    await expect(page.getByText(title)).toBeVisible();

    // 4) Dashboard "today" metrics update — the read is counted (source of truth: the dashboard data).
    await expect
      .poll(async () => {
        const dash = await page.request.get("/api/dashboard").then((r) => r.json());
        return dash?.today?.articlesRead ?? 0;
      })
      .toBeGreaterThanOrEqual(1);

    // ...and the Dashboard page renders for the reader.
    await page.goto("/");
    await expect(page.getByRole("main").getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
  });
});
