import { test, expect } from "../fixtures";
import { engineFeedback, engineRecommendationIds } from "../helpers";

/**
 * Journey 3 — Recommendation Feedback. Drives the real card buttons and verifies persistence through
 * the engine: every feedback type creates a backend row, an ignored card stays gone across a reload
 * (while a mere dislike does not persist-dismiss), and the recommendation ranking is unchanged by
 * feedback (feedback is recorded, never consumed).
 *
 * Cards are scoped by EXACT headline ("…2" is a prefix of "…28") and buttons matched exactly ("Like"
 * is a substring of "Dislike").
 */
test.describe("Recommendation Feedback", () => {
  test("every feedback type persists; ignore survives reload; ranking is unchanged", async ({
    authedPage,
    uid,
  }) => {
    const page = authedPage;
    const idsBefore = await engineRecommendationIds(uid);
    expect(idsBefore.length).toBeGreaterThanOrEqual(2);

    await page.goto("/recommendations");
    const cards = page.locator("article");
    await expect(cards.first()).toBeVisible();

    const headlineOf = (i: number) =>
      cards.nth(i).locator("h3, h2, [class*='font-semibold']").first().innerText();
    const hLiked = (await headlineOf(0)).trim();
    const hIgnored = (await headlineOf(1)).trim();
    expect(hLiked).not.toEqual(hIgnored);

    const cardByHeadline = (h: string) =>
      page.locator("article").filter({ has: page.getByText(h, { exact: true }) });

    // Card A: like + read-later (stay visible), then dislike (dismisses locally, NOT persisted).
    const cardA = cardByHeadline(hLiked);
    await cardA.getByRole("button", { name: "Like", exact: true }).click();
    await cardA.getByRole("button", { name: "Read later", exact: true }).click();
    await cardA.getByRole("button", { name: "Dislike", exact: true }).click();

    // Card B: ignore (persisted + dismissed).
    const cardB = cardByHeadline(hIgnored);
    await cardB.hover();
    await cardB.getByRole("button", { name: "Ignore", exact: true }).click();

    // All four signals persist (poll the engine until the fire-and-forget mutations land).
    await expect
      .poll(async () => (await engineFeedback(uid)).map((f) => f.feedback).sort().join(","))
      .toBe("dislike,ignore,like,read_later");

    // Ranking is unchanged by feedback (recorded, never consumed).
    expect(await engineRecommendationIds(uid)).toEqual(idsBefore);

    // Reload: the IGNORED card stays gone; the DISLIKED (not ignored) card returns.
    await page.reload();
    await expect(cards.first()).toBeVisible();
    await expect(cardByHeadline(hIgnored)).toHaveCount(0);
    await expect(cardByHeadline(hLiked)).toHaveCount(1);
  });

  test("an anonymous feedback POST is rejected with 401", async ({ request }) => {
    const res = await request.post("/api/me/recommendations/feedback", {
      data: { articleId: "https://e2e.example/x", feedback: "like" },
    });
    expect(res.status()).toBe(401);
  });
});
