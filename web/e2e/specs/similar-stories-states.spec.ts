import type { Page, Route } from "@playwright/test";
import { test, expect } from "../fixtures";

/**
 * Similar Stories — the three things an empty card can mean, and the fact that a reader can tell
 * them apart.
 *
 * WHY THIS EXISTS. The card used to render `null` for an empty array, so the section vanished. A
 * similarity threshold shipped an order of magnitude too high, every story returned zero, and the
 * story page simply ended at the coverage list — with nothing on the page to say that a section
 * was missing rather than absent by design. The first person to see it asked whether that was
 * correct behaviour, which is the question a silent gap always produces and can never answer.
 *
 * So the three outcomes now render differently, and this asserts the difference from the reader's
 * side. The section under test moved — it was a horizontal rail at the foot of the page, and is
 * now the Similar Stories card in the story rail — but the rule outlived the component, which is
 * why this file survived the rail's removal rather than being deleted with it.
 *
 * It is deliberately the ONLY thing asserted here: the ranking is measured in Python
 * (`tests/test_story_service.py`), the endpoint in `tests/test_api_fastapi.py`, and the proxy's
 * parameter forwarding in `lib/similar-params.test.ts`. What none of those can see is what the page
 * looks like when the answer is nothing.
 *
 * NOTHING IS SEEDED, and that is deliberate rather than lazy. The specs share ONE `.e2e-tmp`
 * catalog that the engine's real clusterer reads, and `stories-filter-state` asserts absolute facet
 * counts over it ("News carries 2"). The first version of this file seeded one library story with
 * wording unlike any other fixture — and still broke that assertion, because the collision is
 * arithmetic, not clustering: the full suite went from 20 pre-existing failures to 21, and the new
 * one was that count reading 3. Distinctiveness cannot help with a total.
 *
 * So the story page is served entirely from interception: {@link STORY} below is the detail
 * response, and each test supplies the card's. The catalog is never written to and never read, so
 * this file cannot perturb another spec and no other spec's data can change what it asserts —
 * which is also what makes it runnable on its own.
 */

/** The card's own section, whatever it currently renders inside. */
const CARD = 'section[aria-labelledby="similar-panel-heading"]';

/** Any id: nothing resolves it against the catalog. */
const STORY_ID = "st_similarstatesfixture";

/**
 * The story the page renders, in the engine's own response shape (`/api/stories/{id}`).
 *
 * Minimal but COMPLETE for the shape — the page derives publisher stats, the register split and
 * the breakdown panel from `coverage`, so an omitted field shows up as a crashed page rather than
 * as a missing section, and the card under test would then never render at all.
 */
const NOW = new Date();
const HOURS_AGO = (h: number) => new Date(NOW.getTime() - h * 3_600_000).toISOString();
const STORY = {
  id: STORY_ID,
  title: "City library trustees extend Sunday opening hours at three branches",
  summary: "Trustees voted to extend weekend access at the three branch reading rooms.",
  topic: "Politics",
  updatedAt: HOURS_AGO(2),
  totalCoverage: 2,
  publisherCount: 2,
  publishers: ["NPR", "BBC News"],
  publisherDiversity: 1,
  distribution: { left: 0.5, center: 0.5, right: 0 },
  earliest: HOURS_AGO(3),
  latest: HOURS_AGO(2),
  firstPublished: HOURS_AGO(3),
  latestUpdate: HOURS_AGO(2),
  newest: HOURS_AGO(2),
  oldest: HOURS_AGO(3),
  timeSpanHours: 1,
  lowCredibilityPublishers: [],
  coverage: [
    {
      publisher: "NPR",
      headline: "City library trustees extend Sunday opening hours at three branches",
      url: "https://npr-similar.example.com/e2e/library",
      lean: -1,
      leanBucket: "left",
      publishedAt: HOURS_AGO(3),
      publisherLogo: null,
      publisherLogoFallbacks: [],
    },
    {
      publisher: "BBC News",
      headline: "Library trustees extend Sunday opening hours across three branches",
      url: "https://bbc-similar.example.com/e2e/library",
      lean: 0,
      leanBucket: "center",
      publishedAt: HOURS_AGO(2),
      publisherLogo: null,
      publisherLogoFallbacks: [],
    },
  ],
  timeline: [{ date: HOURS_AGO(3), label: "First reported" }],
};

/**
 * Open the fixture story with the card's response under the test's control.
 *
 * `**\/api/stories/*` and `**\/api/stories/*\/similar*` are disjoint — a Playwright `*` does not
 * cross a `/` — so the detail route never swallows the card's request.
 */
async function openStory(page: Page, similar: (route: Route) => unknown): Promise<void> {
  await page.route("**/api/stories/*/similar*", similar);
  await page.route("**/api/stories/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(STORY) }),
  );
  await page.goto(`/stories/${STORY_ID}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("h1", { timeout: 20_000 });
}

test.describe("Similar Stories: an empty card says which kind of empty it is", () => {
  test("no matches renders a stated absence, not a missing section", async ({ authedPage }) => {
    await openStory(authedPage, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ stories: [], total: 0 }),
      }),
    );
    const card = authedPage.locator(CARD);

    await expect(card, "the section stays on the page").toBeVisible();
    await expect(card).toContainText("Similar Stories");
    await expect(card, "and says why it is empty").toContainText(/Nothing else in the catalog covers this event/i);
    await expect(card.locator("li"), "with no cards invented to fill it").toHaveCount(0);
  });

  test("a failed request offers a retry and never claims nothing is similar", async ({ authedPage }) => {
    await openStory(authedPage, (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "engine_unavailable", message: "down" } }),
      }),
    );
    const card = authedPage.locator(CARD);

    // The query retries a 5xx with backoff, so the failure surfaces after several seconds — and
    // until it does the card is correctly still LOADING. That wait is the behaviour, not a flake.
    await expect(card.getByRole("button", { name: /try again/i })).toBeVisible({ timeout: 25_000 });
    await expect(card).toContainText(/couldn't load related coverage/i);
    await expect(
      card,
      "a request that failed is not evidence that nothing is similar",
    ).not.toContainText(/Nothing else in the catalog covers this event/i);
  });

  test("while the request is in flight it shows neither cards nor the empty line", async ({ authedPage }) => {
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await openStory(authedPage, async (route) => {
      await held;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ stories: [], total: 0 }),
      });
    });
    const card = authedPage.locator(CARD);

    await expect(card).toBeVisible();
    await expect(
      card,
      "an answer that has not arrived is not an answer of none",
    ).not.toContainText(/Nothing else in the catalog covers this event/i);
    await expect(card).not.toContainText(/couldn't load related coverage/i);

    // …and once it lands, the empty line does appear.
    release();
    await expect(card).toContainText(/Nothing else in the catalog covers this event/i, { timeout: 20_000 });
  });
});
