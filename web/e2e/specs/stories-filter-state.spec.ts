import path from "node:path";
import { execFileSync } from "node:child_process";
import { test, expect } from "../fixtures";

/**
 * Stories → a story → Back must come back to the SAME filtered view.
 *
 * It did not. The six filters were six `useState`s inside `StoryBrowser`, so a selection lived
 * only as long as that component stayed mounted; opening a story unmounts it and coming back
 * mounts a fresh one at its defaults. Three of them were already read FROM the URL as deep-link
 * entry points, but nothing ever wrote back, so even those reset the moment a reader picked them
 * by hand instead of arriving via a link.
 *
 * The filters now live in the URL, which is the state the browser already restores on Back. These
 * tests therefore assert BOTH halves — that a UI change reaches the URL, and that the controls
 * come back showing it — because either one alone can pass while the reader still sees a reset
 * page.
 */
const WEB_DIR = path.join(__dirname, "..", "..");
const REPO_ROOT = path.join(WEB_DIR, "..");
const DB = path.join(WEB_DIR, ".e2e-tmp", "engine.db");

let seeded = false;

/**
 * TWO events, seeded together before any page load.
 *
 * Together, and up front, because the engine caches its story build: rows inserted after a later
 * test has already requested /api/stories do not appear in the cached view, so a fixture seeded
 * mid-run is simply absent from the page. (Measured — the Type test failed with zero cards for its
 * own event while every other test passed.) One seeding call, ahead of everything, keeps every test
 * looking at the same catalogue.
 *
 *   harbour   NPR + New York Post — both plain news outlets, and a left/right pair for the
 *             Covered-by assertions.
 *   exoplanet Nature + BBC News — Nature is `kind = research` in the outlet registry, which is the
 *             ONLY thing the Type filter reads. Real registry names are the point: an invented
 *             outlet resolves to nothing, and every arm of the Type assertions would then pass
 *             against a filter that never ran. Pairing it with a news outlet also makes the event a
 *             News story too, which is the correct reading of a cluster covered by both.
 *
 * Two near-identical headlines per event — the shape that reliably clusters.
 *
 * Published RELATIVE TO NOW, deliberately. `story_service` only clusters a 6-day window
 * (`scan_days`), so a fixture written with a hardcoded date works until that date ages past the
 * window and then fails forever with "the seeded pair must cluster into a story" — which is
 * exactly what `story-continuation.spec.ts` (2026-08-03) and `coverage-comparison.spec.ts`
 * (2026-08-02) do today against a window that now starts 2026-08-04. A relative date cannot rot.
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
desc = "The harbour authority released its dredging schedule on Tuesday morning. " * 3
sci = "The array resolved the planet's atmosphere across four observing runs. " * 3
members = [
    ("NPR", "Harbour authority releases its long delayed dredging schedule",
     "https://npr-filters.example.com/e2e/harbour", -1.0, 3, "Politics", True, desc),
    ("New York Post", "Harbour authority releases long delayed dredging schedule",
     "https://nypost-filters.example.com/e2e/harbour", 2.0, 2, "Politics", True, desc),
    ("Nature", "Telescope array resolves the atmosphere of a distant exoplanet",
     "https://nature-filters.example.com/e2e/exoplanet", 0.0, 3, "Science", False, sci),
    ("BBC News", "Telescope array resolves atmosphere of distant exoplanet",
     "https://bbc-filters.example.com/e2e/exoplanet", 0.0, 2, "Science", False, sci),
]
for pub, headline, url, lean, hours_ago, cat, political, body_desc in members:
    st.upsert_feed_article(
        canonical_url=ingest.canonical_url(url), url=url, publisher=pub, source_publisher=None,
        title=headline, description=body_desc, body=None,
        published_at=(now - timedelta(hours=hours_ago)).isoformat(), source_feed="e2e",
        scored={"article_id": url, "outlet": pub, "category": cat,
                "topic": cat, "lean": lean, "political": political})
`;
  execFileSync("python", ["-c", py], { cwd: REPO_ROOT, stdio: "pipe" });
}

/**
 * Open a FilterSelect by its visible label and choose an option.
 *
 * The option is matched by name plus an OPTIONAL trailing count, because a filter that shows facet
 * counts folds the number into each row's accessible name — "Research 1", not "Research". That is
 * the right name for a screen reader to announce, so the matcher accommodates it rather than the
 * component hiding it. Still anchored at both ends, so "News" cannot select "Newsletters".
 */
async function pick(page: import("@playwright/test").Page, label: string, option: string) {
  await page.getByRole("button", { name: new RegExp(`^${label}`) }).click();
  await page.getByRole("menuitemradio", { name: new RegExp(`^${option}(\\s+\\d+)?$`) }).click();
}

test.describe("Stories filter state survives a round trip", () => {
  test("filters chosen in the UI are restored after opening a story and going Back", async ({
    authedPage,
  }) => {
    seedStory();
    await authedPage.goto("/stories", { waitUntil: "networkidle" });

    // Two filters that are always present regardless of what the catalog holds: "Covered by" and
    // Sort are static option lists, so this test does not depend on facet data to exercise the
    // mechanism. The control is labelled "Covered by"; its URL parameter is still `lean`, and
    // every assertion on that parameter below is deliberately unchanged.
    await pick(authedPage, "Covered by", "Left");
    await pick(authedPage, "Sort", "Latest");

    await expect(authedPage, "a UI change reaches the URL").toHaveURL(/lean=left/);
    await expect(authedPage).toHaveURL(/sort=latest/);
    const filtered = new URL(authedPage.url());

    // Leave for a story exactly the way a reader does.
    const storyId = await authedPage.evaluate(async () => {
      const res = await fetch("/api/stories?limit=200");
      const body = (await res.json()) as { stories?: { id: string }[] };
      return body.stories?.[0]?.id ?? null;
    });
    expect(storyId, "the seeded pair must cluster into a story to open").not.toBeNull();
    await authedPage.goto(`/stories/${storyId}`, { waitUntil: "networkidle" });

    await authedPage.goBack({ waitUntil: "networkidle" });

    // Both halves. The URL coming back is necessary but not sufficient — the controls have to be
    // rendering FROM it, which is the thing that was broken.
    await expect(authedPage).toHaveURL(new RegExp(`${filtered.search.replace(/[?&=]/g, "\\$&")}$`));
    await expect(
      authedPage.getByRole("button", { name: /^Covered by/ }),
      "the Covered by control still shows Left",
    ).toContainText("Left");
    await expect(
      authedPage.getByRole("button", { name: /^Sort/ }),
      "the Sort control still shows Latest",
    ).toContainText("Latest");
  });

  test("a bare /stories still loads the defaults", async ({ authedPage }) => {
    // The other half of the contract: persistence must not turn into stickiness. Arriving with no
    // params is a fresh, unfiltered view.
    await authedPage.goto("/stories", { waitUntil: "networkidle" });
    await expect(authedPage).toHaveURL(/\/stories$/);
    // An inactive FilterSelect renders its label alone; an active one appends "· <value>".
    await expect(authedPage.getByRole("button", { name: /^Covered by/ })).not.toContainText("·");
    // Sort is never "all" — it always has a value — so it always shows one. The contract here is
    // that the value is the DEFAULT, not that the control looks unset.
    await expect(authedPage.getByRole("button", { name: /^Sort/ })).toContainText("Top");
  });

  test("resetting a filter cleans the parameter out of the URL", async ({ authedPage }) => {
    // Otherwise Back would restore /stories?lean=all, which is a filter the engine would be asked
    // to apply as a literal value rather than read as "no filter".
    await authedPage.goto("/stories", { waitUntil: "networkidle" });
    await pick(authedPage, "Covered by", "Left");
    await expect(authedPage).toHaveURL(/lean=left/);
    await pick(authedPage, "Covered by", "All");
    await expect(authedPage).not.toHaveURL(/lean=/);
  });

  test("the Type filter narrows the page to a curated source type", async ({ authedPage }) => {
    // The Type lens reads the outlet registry's curated `kind`, so the fixture uses REAL registry
    // names: Nature is `kind = research`, BBC News a plain news outlet. Invented outlets resolve to
    // nothing, and every arm of this test would then look correct against a filter that did nothing.
    seedStory();
    await authedPage.goto("/stories", { waitUntil: "networkidle" });

    const cards = authedPage.getByRole("heading", { level: 3 });
    await expect(cards.filter({ hasText: /Telescope/ })).toHaveCount(1);
    const before = await cards.count();
    expect(before, "both seeded events are on the page to begin with").toBeGreaterThan(1);

    await pick(authedPage, "Type", "Research");
    await expect(authedPage, "the choice reaches the URL like every other filter").toHaveURL(
      /type=research/,
    );

    // The narrowing itself — the research event stays, the news-only one goes.
    await expect(cards.filter({ hasText: /Telescope/ })).toHaveCount(1);
    await expect(cards.filter({ hasText: /[Hh]arbour/ })).toHaveCount(0);

    // …and resetting cleans the parameter out, so Back never restores `type=all` as a literal.
    await pick(authedPage, "Type", "All");
    await expect(authedPage).not.toHaveURL(/type=/);
    await expect(cards.filter({ hasText: /[Hh]arbour/ })).toHaveCount(1);
  });

  test("each Type option carries the count it would return", async ({ authedPage }) => {
    seedStory();
    await authedPage.goto("/stories", { waitUntil: "networkidle" });
    await authedPage.getByRole("button", { name: /^Type/ }).click();

    // Two seeded events. Both are covered by a curated news outlet, and one of them also by Nature,
    // so News reads 2 and Research 1 — the counts are "has coverage from this type", not a
    // partition, which is why they sum to more than the two stories on the page.
    const row = (name: string) => authedPage.getByRole("menuitemradio", { name: new RegExp(`^${name}`) });
    await expect(row("News")).toContainText("2");
    await expect(row("Research")).toContainText("1");
    // The empty lens says 0 rather than vanishing — a fixed three-option list whose contents came
    // and went between page states would read as a broken control.
    await expect(row("Community"), "an empty type still reports").toContainText("0");

    // And the number is the result: choosing Research leaves exactly that many cards.
    await row("Research").click();
    await expect(authedPage).toHaveURL(/type=research/);
    await expect(authedPage.getByRole("heading", { level: 3 })).toHaveCount(1);
  });

  test("the existing ?country= deep link still preselects", async ({ authedPage }) => {
    // The three deep links (country / publisher / blindspot) are entry points other surfaces rely
    // on; moving the filters into the URL must not disturb them.
    await authedPage.goto("/stories?publisher=NPR", { waitUntil: "networkidle" });
    await expect(authedPage.getByRole("button", { name: /^Publisher/ })).toContainText("NPR");
  });
});
