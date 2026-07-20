import { test, expect } from "../fixtures";
import { seedOnboarding, seedReads } from "../helpers";
import { MEASURED_MIN_READS } from "../constants";

/**
 * Journey 4 — Information Health Report. With onboarding but no reads a reader sees the Initial
 * Estimate (the pre-activity state); once they cross the measured-reads threshold the report becomes
 * Measured with personalized metrics. The report `mode` (from the same `/api/report` the page renders)
 * is the authoritative discriminator — the page renders both states with the same layout.
 */
test.describe("Information Health Report", () => {
  test("shows the estimate before activity and personalized metrics after", async ({ authedPage, uid }) => {
    const page = authedPage;
    const reportMode = async () =>
      (await page.request.get("/api/report").then((r) => r.json())).mode as string;

    // A genuine Initial Estimate (onboarding present, no reads yet) — not the demo fallback.
    await seedOnboarding(uid);
    await page.goto("/report");
    await expect(page.getByRole("main").getByRole("heading", { name: "Health Report", exact: true })).toBeVisible();
    expect(await reportMode()).toBe("estimate"); // empty-activity state: estimate, not measured

    // Cross the measured threshold → personalized (measured) metrics appear.
    await seedReads(uid, MEASURED_MIN_READS);
    expect(await reportMode()).toBe("measured");
    await page.goto("/report");
    await expect(page.getByRole("main").getByRole("heading", { name: "Health Report", exact: true })).toBeVisible();
    // Axis confidence is part of the measured, personalized report.
    await expect(page.getByText("Axis confidence")).toBeVisible();
  });
});
