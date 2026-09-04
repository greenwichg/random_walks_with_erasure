import { execFileSync } from "node:child_process";
import path from "node:path";
import { test, expect } from "../fixtures";

/**
 * Similar Stories — the three things an empty rail can mean, and the fact that a reader can tell
 * them apart.
 *
 * WHY THIS EXISTS. The rail used to render `null` for an empty array, so the section vanished. A
 * similarity threshold shipped an order of magnitude too high, every story returned zero, and the
 * story page simply ended at the coverage list — with nothing on the page to say that a section
 * was missing rather than absent by design. The first person to see it asked whether that was
 * correct behaviour, which is the question a silent gap always produces and can never answer.
 *
 * So the three outcomes now render differently, and this asserts the difference from the reader's
 * side. It is deliberately the ONLY assertion here: the ranking is measured in Python
 * (`tests/test_story_service.py`), the endpoint in `tests/test_api_fastapi.py`, and the proxy's
 * parameter forwarding in `lib/similar-params.test.ts`. What none of those can see is what the
 * page looks like when the answer is nothing.
 *
 * The responses are INTERCEPTED rather than seeded. The specs share one `.e2e-tmp` catalog that
 * the real clusterer reads, so seeding a story with deliberately no relatives would mean seeding
 * something unlike everything else in that catalog — and then depending on the clusterer to keep
 * agreeing that it is unlike them, which is a second thing to break. Interception makes the
 * engine's answer the parameter of the test, which is exactly what is under test here.
 */

/** The rail's own section, whatever it currently renders inside. */
const RAIL = 'section[aria-labelledby="similar-stories-heading"]';

const WEB_DIR = path.join(__dirname, "..", "..");
const REPO_ROOT = path.join(WEB_DIR, "..");
const DB = path.join(WEB_DIR, ".e2e-tmp", "engine.db");

let seeded = false;

/**
 * One event, so this spec has a story page to open when it runs alone.
 *
 * Wording chosen to share nothing with any other spec's fixture — the specs write into ONE
 * `.e2e-tmp` catalog that the engine's real clusterer reads, and a near-duplicate headline joins
 * another spec's story and changes the counts that spec asserts on. Published relative to now,
 * because `story_service` clusters a 6-day window and a hardcoded date works until it ages out of
 * it and then fails forever.
 *
 * What is rendered inside the rail never depends on this fixture: the responses are intercepted.
 * It exists only so that a story page exists.
 */
function seedStory(): void {
  if (seeded) return;
  seeded = true;
  const py = `
import sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, ${JSON.stringify(path.join(REPO_ROOT, "examples"))})
import ingest
import store as store_mod

st = store_mod.Store("sqlite:///" + ${JSON.stringify(DB)})
now = datetime.now(timezone.utc)
desc = "Trustees voted to extend weekend access at the three branch reading rooms. " * 3
members = [
    ("NPR", "City library trustees extend Sunday opening hours at three branches",
     "https://npr-similar.example.com/e2e/library", -1.0, 3),
    ("BBC News", "Library trustees extend Sunday opening hours across three branches",
     "https://bbc-similar.example.com/e2e/library", 0.0, 2),
]
for pub, headline, url, lean, hours_ago in members:
    st.upsert_feed_article(
        canonical_url=ingest.canonical_url(url), url=url, publisher=pub, source_publisher=None,
        title=headline, description=desc, body=None,
        published_at=(now - timedelta(hours=hours_ago)).isoformat(), source_feed="e2e",
        scored={"article_id": url, "outlet": pub, "category": "Politics",
                "topic": "Politics", "lean": lean, "political": True})
`;
  execFileSync("python", ["-c", py], { cwd: REPO_ROOT, stdio: "pipe" });
}

/**
 * A story id to open — ANY story, not this spec's own.
 *
 * Polled rather than read once, because the engine serves a stale build while it rebuilds behind
 * the reader (`story_service._cached_build`): the request right after a seed can legitimately
 * answer from the pre-seed catalog. Waiting for the rebuild is the contract, not a workaround.
 */
async function openAStory(page: import("@playwright/test").Page): Promise<string> {
  seedStory();
  await page.goto("/stories", { waitUntil: "networkidle" });
  let id: string | null = null;
  for (let attempt = 0; attempt < 12 && id === null; attempt++) {
    id = await page.evaluate(async () => {
      const res = await fetch("/api/stories?limit=200", { cache: "no-store" });
      const body = (await res.json()) as { stories?: { id: string }[] };
      return body.stories?.[0]?.id ?? null;
    });
    if (id === null) await page.waitForTimeout(1000);
  }
  expect(id, "the catalog must hold at least one story to open a story page").not.toBeNull();
  return id as string;
}

test.describe("Similar Stories: an empty rail says which kind of empty it is", () => {
  test("no matches renders a stated absence, not a missing section", async ({ authedPage }) => {
    const id = await openAStory(authedPage);
    await authedPage.route("**/api/stories/*/similar*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ stories: [], total: 0 }),
      }),
    );

    await authedPage.goto(`/stories/${id}`, { waitUntil: "networkidle" });
    const rail = authedPage.locator(RAIL);

    await expect(rail, "the section stays on the page").toBeVisible();
    await expect(rail).toContainText("Similar Stories");
    await expect(rail, "and says why it is empty").toContainText(/Nothing else in the catalog covers this event/i);
    await expect(rail.locator("li"), "with no cards invented to fill it").toHaveCount(0);
  });

  test("a failed request offers a retry and never claims nothing is similar", async ({ authedPage }) => {
    const id = await openAStory(authedPage);
    await authedPage.route("**/api/stories/*/similar*", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "engine_unavailable", message: "down" } }),
      }),
    );

    await authedPage.goto(`/stories/${id}`, { waitUntil: "domcontentloaded" });
    const rail = authedPage.locator(RAIL);

    // The query retries a 5xx with backoff, so the failure surfaces after several seconds — and
    // until it does the rail is correctly still LOADING. That wait is the behaviour, not a flake.
    await expect(rail.getByRole("button", { name: /try again/i })).toBeVisible({ timeout: 25_000 });
    await expect(rail).toContainText(/couldn't load related coverage/i);
    await expect(
      rail,
      "a request that failed is not evidence that nothing is similar",
    ).not.toContainText(/Nothing else in the catalog covers this event/i);
  });

  test("while the request is in flight it shows neither cards nor the empty line", async ({ authedPage }) => {
    const id = await openAStory(authedPage);
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await authedPage.route("**/api/stories/*/similar*", async (route) => {
      await held;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ stories: [], total: 0 }),
      });
    });

    await authedPage.goto(`/stories/${id}`, { waitUntil: "domcontentloaded" });
    const rail = authedPage.locator(RAIL);
    await expect(rail).toBeVisible();
    await expect(
      rail,
      "an answer that has not arrived is not an answer of none",
    ).not.toContainText(/Nothing else in the catalog covers this event/i);
    await expect(rail).not.toContainText(/couldn't load related coverage/i);

    // …and once it lands, the empty line does appear.
    release();
    await expect(rail).toContainText(/Nothing else in the catalog covers this event/i, { timeout: 20_000 });
  });
});
