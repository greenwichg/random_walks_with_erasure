import { test, expect } from "../fixtures";

/**
 * Journey 5 — Settings. The theme choice (its own instant write-through) and a saved preference (via
 * the Save button) both survive a reload. Persistence is asserted by waiting for the settings POST,
 * then reloading and checking the restored state.
 */
test.describe("Settings", () => {
  test("theme choice persists across a reload", async ({ authedPage }) => {
    const page = authedPage;
    await page.goto("/settings");
    const darkBtn = page.getByRole("button", { name: "Dark", exact: true });
    await expect(darkBtn).toBeVisible();

    // applyTheme writes through to the account; wait for that persist so a fresh load can restore it.
    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/settings") && r.request().method() === "POST"),
      darkBtn.click(),
    ]);
    await expect(page.locator("html")).toHaveClass(/dark/);

    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);
  });

  test("a preference change saves and survives a reload", async ({ authedPage }) => {
    const page = authedPage;
    await page.goto("/settings");

    // The "Weekly report" toggle row (the tightest div holding both its label and its switch).
    const rowFor = (label: string) =>
      page
        .locator("div")
        .filter({ has: page.getByText(label, { exact: true }) })
        .filter({ has: page.getByRole("switch") })
        .last();
    const toggle = rowFor("Weekly report").getByRole("switch");
    await expect(toggle).toBeVisible();
    const before = await toggle.getAttribute("aria-checked");

    await toggle.click();
    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/settings") && r.request().method() === "POST"),
      page.getByRole("button", { name: "Save changes" }).click(),
    ]);

    // Reload: the switch reflects the persisted (flipped) value.
    await page.reload();
    const after = await rowFor("Weekly report").getByRole("switch").getAttribute("aria-checked");
    expect(after).not.toBe(before);
  });
});
