import { test, expect } from "../fixtures";
import { engineGet } from "../helpers";

/**
 * PA1 — end-to-end proof of the product-analytics pipeline: a signed-in reader browses instrumented
 * surfaces; the client `track()` buffer flushes to the `/api/events` proxy, which forwards to the
 * engine sink, which stores the events; the internal dashboard then reports them in the funnel. This
 * exercises the REAL stack (no stubs): client provider → proxy (server-resolved identity) → sink →
 * store → funnel maths.
 */
test.describe("Product Analytics (PA1)", () => {
  test("client events flow through the pipeline into the activation funnel", async ({ authedPage, uid }) => {
    // Visit instrumented surfaces. app_opened (once/session) + page_viewed + health_report_viewed
    // fire on /report; recommendations_viewed on /recommendations. The client buffers and flushes.
    await authedPage.goto("/report", { waitUntil: "networkidle" });
    await authedPage.waitForTimeout(1500);
    await authedPage.goto("/recommendations", { waitUntil: "networkidle" });
    await authedPage.waitForTimeout(4000); // let the 3s flush timer fire: buffer → beacon → sink

    // The internal (dev-trusted) event dashboard must now show this reader's events.
    await expect
      .poll(
        async () => {
          const counts = await engineGet<{ byEvent: Record<string, number> }>(uid, "/api/analytics/events");
          return counts.byEvent?.health_report_viewed ?? 0;
        },
        { timeout: 15000, intervals: [1000] },
      )
      .toBeGreaterThan(0);

    // …and the funnel derives real reachers from those events.
    const funnel = await engineGet<{ stages: { key: string; reachers: number }[] }>(
      uid,
      "/api/analytics/funnel",
    );
    const by = Object.fromEntries(funnel.stages.map((s) => [s.key, s.reachers]));
    expect(by.app_opened).toBeGreaterThan(0);
    expect(by.health_report_viewed).toBeGreaterThan(0);
  });
});
