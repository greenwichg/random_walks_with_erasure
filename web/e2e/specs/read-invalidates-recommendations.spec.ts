import { test, expect } from "../fixtures";
import { seedReads } from "../helpers";

/**
 * P3 (2026-08-02) — a recorded read marks the recommendations feed stale.
 *
 * The engine reflects a read on the very next fetch (the personal model rebuilds at the new
 * reading version, ~50 ms measured), but `useRecordRead` never invalidated `["recommendations"]`,
 * and the app's query defaults are staleTime 60 s with no focus refetch — so a reader who read an
 * article and returned to Recommendations inside that minute was served the PRE-read feed from
 * cache, with no network request at all.
 *
 * EVERY navigation here is a SOFT one (sidebar link clicks), and that is the whole design of the
 * test: `page.goto` is a full document load that throws the QueryClient away, so a fresh fetch
 * afterwards proves nothing — the first draft of this test passed against the un-fixed hook for
 * exactly that reason. Under soft navigation the cache survives, React Query does not refetch a
 * FRESH query on mount, focus refetch is off and there is no polling. The second
 * GET /api/recommendations therefore has exactly one possible cause: the onSettled invalidation.
 * Mutation-verified — with that line reverted this test times out.
 *
 * The read is recorded through `useRecordRead`, the single pipeline every surface shares
 * (`openedFrom` is metadata the engine never branches on), so this covers the Discover flow;
 * /history is used because it reliably has rows in this suite's catalog-less engine.
 */
test("a recorded read marks the recommendations feed stale, not 60s-cached", async ({
  authedPage: page,
  uid,
}) => {
  await seedReads(uid, 6); // rows for /history to render

  // The Read button opens the article in a new tab; seeded urls are unreachable by design. Abort
  // every non-localhost navigation so the popup neither hangs the test nor hits the network.
  await page.context().route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (r) => r.abort());

  const nav = (name: string) => page.getByRole("link", { name, exact: true }).first();

  // 1. One hard load to enter the app, then prime the feed by SOFT-navigating to it.
  await page.goto("/history");
  const primed = page.waitForResponse(
    (r) => r.url().includes("/api/recommendations") && r.request().method() === "GET",
  );
  await nav("Recommendations").click();
  await primed;                                  // the feed is now cached and 60 s fresh

  // 2. Soft-navigate back and record a read. The QueryClient — and its fresh feed entry — persist.
  await nav("Reading History").click();
  const read = page.getByTitle("Open the article and record it as read").first();
  await expect(read).toBeVisible();
  const clickedAt = Date.now();
  await read.click();
  // Stay on the page while the mutation settles — exactly what a real reader does, since the
  // article opens in a NEW TAB and this one stays mounted. (React Query does not run a
  // component's onSettled if that component unmounted first, so navigating away inside the
  // 700 ms beacon wait would silently drop the invalidation — worth knowing, and the reason
  // this wait is here rather than a race.)
  await page.waitForTimeout(1200);

  // 3. Soft-navigate back INSIDE the freshness window. Only the invalidation can make the query
  //    stale, and only a stale query refetches on mount.
  const refetch = page.waitForResponse(
    (r) => r.url().includes("/api/recommendations") && r.request().method() === "GET",
    { timeout: 10_000 },
  );
  await nav("Recommendations").click();
  const res = await refetch;
  expect(res.ok()).toBeTruthy();
  console.log(`read -> updated recommendations visible: ${Date.now() - clickedAt} ms`);
});
