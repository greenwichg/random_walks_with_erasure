import { execSync } from "node:child_process";
import path from "node:path";
import { test, expect } from "../fixtures";

/**
 * Story Continuation (docs/STORY_CONTINUATION_DESIGN.md) — the return moment, end to end.
 *
 * What only an e2e can prove: the strip is triggered by a REAL `visibilitychange` after a REAL
 * dwell, and its dismissal survives a REAL reload. Everything upstream of that — the engine's nine
 * gates, the storage contract, the dwell arithmetic — is covered by tests that do not need a
 * browser (`tests/test_story_continuation.py`, `tests/test_api_fastapi.py`, `lib/continuation.test.ts`).
 *
 * The offer is ARMED DIRECTLY rather than by clicking Read. Driving the real click would need the
 * engine's flag on, a clustered story with an opposing rated pair, and a popup-blocked
 * `window.open` — three things that make the test about seeding rather than about the return, and
 * that fail for reasons unrelated to what is being asserted. Arming is the seam the design already
 * defines (§6.2: sessionStorage `hv.continue.armed`), so writing it is using the contract, not
 * bypassing it.
 *
 * The anchor article IS really seeded, though, because the strip is mounted per card and keyed to
 * that card's URL — arming a URL no card carries would assert nothing at all, which is exactly what
 * the first draft of this spec did.
 */

const WEB_DIR = path.join(__dirname, "..", "..");
const REPO_ROOT = path.join(WEB_DIR, "..");
const DB = path.join(WEB_DIR, ".e2e-tmp", "engine.db");

const ARMED_KEY = "hv.continue.armed";
const STATE_KEY = "hv.continue";

/** The one anchor this file seeds. Fixed rather than uid-scoped, and seeded ONCE: specs share a
 *  single .e2e-tmp catalog that the engine's real clusterer reads, so seeding five near-identical
 *  articles (one per test) put four needless rows into every other spec's clustering input. Test
 *  isolation does not need them — each test gets a fresh browser context, so its dismissal and
 *  impression state is already its own.
 *
 *  (This was investigated as a cause of coverage-comparison's whole-suite failure and is NOT one:
 *  that spec fails identically with this whole file excluded, along with saved and
 *  recommendation-feedback. All three are pre-existing.) */
const ANCHOR_URL = "https://cbs-continuation.example.com/e2e/spectrum";
let seeded = false;

/** Seed the anchor into the real catalog so a Discover card actually carries this URL.
 *  Its wording is deliberately unlike any other spec's fixture: specs share one `.e2e-tmp/engine.db`
 *  and the engine's real clusterer decides membership, so a near-duplicate headline would silently
 *  JOIN another spec's story and change the counts it asserts on. The first draft of this file
 *  reused coverage-comparison's harbour wording and broke exactly that spec. */
function seedAnchor(url: string): void {
  if (seeded) return;
  seeded = true;
  const py = `
import sys
sys.path.insert(0, ${JSON.stringify(path.join(REPO_ROOT, "examples"))})
import ingest
import store as store_mod

st = store_mod.Store("sqlite:///" + ${JSON.stringify(DB)})
url = ${JSON.stringify(url)}
title = "Regulator publishes the annual spectrum auction timetable for bidders"
st.upsert_feed_article(
    canonical_url=ingest.canonical_url(url), url=url, publisher="CBS News",
    source_publisher=None, title=title,
    description="The regulator set out its spectrum auction timetable on Tuesday morning. " * 3,
    body=None, published_at="2026-08-03T12:00:00Z", source_feed="e2e",
    scored={"article_id": url, "outlet": "CBS News", "category": "Politics",
            "lean": -1.0, "political": True, "title": title})
print("seeded")
`;
  execSync("python3 -", { input: py, stdio: ["pipe", "inherit", "inherit"] });
}

/** A real two-publisher cluster, so the Stories page has a story whose coverage list carries the
 *  anchor. Distinct wording from the Discover fixture above and from every other spec's. */
const STORY_ANCHOR = "https://cbs-story.example.com/e2e/levee";
let storySeeded = false;

function seedStoryCluster(): void {
  if (storySeeded) return;
  storySeeded = true;
  const py = `
import sys
sys.path.insert(0, ${JSON.stringify(path.join(REPO_ROOT, "examples"))})
import ingest
import store as store_mod

st = store_mod.Store("sqlite:///" + ${JSON.stringify(DB)})
desc = "The levee inspection board published its findings on Wednesday afternoon. " * 3
members = [
    ("CBS News", "Levee inspection board publishes its long awaited findings report",
     ${JSON.stringify(STORY_ANCHOR)}, -1.0, "08"),
    ("Fox News", "Levee inspection board publishes long awaited findings in report",
     "https://fox-story.example.com/e2e/levee", 2.0, "09"),
]
for pub, headline, url, lean, hh in members:
    st.upsert_feed_article(
        canonical_url=ingest.canonical_url(url), url=url, publisher=pub, source_publisher=None,
        title=headline, description=desc, body=None,
        published_at=f"2026-08-03T{hh}:00:00Z", source_feed="e2e",
        scored={"article_id": url, "outlet": pub, "category": "Politics",
                "lean": lean, "political": True, "title": headline})
print("seeded story cluster")
`;
  execSync("python3 -", { input: py, stdio: ["pipe", "inherit", "inherit"] });
}

const OFFER = {
  storyId: "s-e2e-spectrum",
  storyTitle: "Regulator publishes the spectrum auction timetable",
  outlets: 9,
  anchor: { url: "", publisher: "CBS News", lean: -1, leanBucket: "left" },
  sibling: {
    url: "https://fox.example.com/e2e/spectrum",
    publisher: "Fox News",
    headline: "Regulator sets out the auction timetable",
    lean: 2,
    leanBucket: "right",
    publishedAt: "2026-08-03T09:00:00Z",
  },
  distance: 3,
  candidateCount: 5,
};

/** Arm a candidate for `anchorUrl` as the Read click would, `agoMs` in the past. */
async function arm(
  page: import("@playwright/test").Page,
  anchorUrl: string,
  agoMs = 0,
): Promise<void> {
  const offerFor = { ...OFFER, anchor: { ...OFFER.anchor, url: anchorUrl } };
  await page.evaluate(
    ([key, offer, ago]) => {
      window.sessionStorage.setItem(
        key as string,
        JSON.stringify({
          anchorUrl: (offer as { anchor: { url: string } }).anchor.url,
          armedAt: Date.now() - (ago as number),
          offer,
        }),
      );
    },
    [ARMED_KEY, offerFor, agoMs] as const,
  );
  // A reload after writing, deliberately. In the app the write goes through `armCandidate`, which
  // notifies the in-memory subscribers a mounted card is listening on; writing sessionStorage from
  // the page context skips that, so without a reload the card never learns it is armed and the
  // spec would assert against a component that was never enabled. Reloading is also the real mobile
  // path the two-tier storage exists for (§6.2).
  await page.reload({ waitUntil: "networkidle" });
}

/** Make `document.visibilityState` writable and go hidden, as `window.open` does. */
async function goHidden(page: import("@playwright/test").Page): Promise<void> {
  await page.evaluate(() => {
    const doc = document as Document & { __vis?: string };
    Object.defineProperty(doc, "visibilityState", {
      configurable: true,
      get: () => doc.__vis ?? "visible",
    });
    doc.__vis = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

/** Come back after `hiddenMs`, without actually waiting that long. */
async function comeBack(page: import("@playwright/test").Page, hiddenMs: number): Promise<void> {
  await page.evaluate((ms) => {
    const doc = document as Document & { __vis?: string };
    const realNow = Date.now;
    Date.now = () => realNow() + (ms as number);
    doc.__vis = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
    Date.now = realNow;
  }, hiddenMs);
}

/** A full hidden→visible cycle with a controlled dwell, as returning from the publisher's tab. */
async function returnAfter(page: import("@playwright/test").Page, hiddenMs: number): Promise<void> {
  await goHidden(page);
  await comeBack(page, hiddenMs);
}

test.describe("Story Continuation", () => {
  test("appears only after a real absence, and a short one is ignored", async ({ authedPage }) => {
    const anchor = ANCHOR_URL;
    seedAnchor(anchor);
    await authedPage.goto("/discover", { waitUntil: "networkidle" });
    // The strip is mounted per card and keyed to that card's URL, so the anchor must really be on
    // the page — otherwise every assertion below would pass against nothing.
    await expect(
      authedPage.getByText("Regulator publishes the annual spectrum auction timetable for bidders").first(),
    ).toBeVisible();
    await arm(authedPage, anchor);

    // A 4 s flick to another tab is not a read (§2.1's dwell gate).
    await returnAfter(authedPage, 4_000);
    await expect(authedPage.getByText("Compare this story")).toBeHidden();

    // …a real absence is.
    await returnAfter(authedPage, 25_000);
    const strip = authedPage.getByText("Compare this story");
    await expect(strip).toBeVisible();

    // Both outlets named on the same axis — never the sibling alone (§1.3.3).
    await expect(authedPage.getByText(/Fox News is rated right of centre/)).toBeVisible();
    await expect(authedPage.getByText(/you read an account rated left of centre/)).toBeVisible();
    await expect(authedPage.getByText(/9 outlets covered this event/)).toBeVisible();
    await expect(authedPage.getByRole("button", { name: "Read another perspective" })).toBeVisible();
  });

  test("dismissal survives a reload, permanently for that story", async ({ authedPage }) => {
    const anchor = ANCHOR_URL;
    seedAnchor(anchor);
    await authedPage.goto("/discover", { waitUntil: "networkidle" });
    await arm(authedPage, anchor);
    await returnAfter(authedPage, 25_000);
    await expect(authedPage.getByText("Compare this story")).toBeVisible();

    await authedPage.getByLabel("Dismiss this comparison").click();
    await expect(authedPage.getByText("Compare this story")).toBeHidden();

    // Re-arm the SAME story and return again: a dismissal that only lasted the session would be
    // nagging by another name (§6.1).
    await arm(authedPage, anchor);
    await returnAfter(authedPage, 25_000);
    await expect(authedPage.getByText("Compare this story")).toBeHidden();

    const state = await authedPage.evaluate(
      (k) => JSON.parse(window.localStorage.getItem(k as string) ?? "{}"),
      STATE_KEY,
    );
    expect(state[OFFER.storyId]?.d).toBe(1);
  });

  test("stops after two impressions without engagement", async ({ authedPage }) => {
    const anchor = ANCHOR_URL;
    seedAnchor(anchor);
    await authedPage.goto("/discover", { waitUntil: "networkidle" });

    for (const attempt of [1, 2]) {
      await arm(authedPage, anchor);
      await returnAfter(authedPage, 25_000);
      await expect(
        authedPage.getByText("Compare this story"),
        `impression ${attempt} should render`,
      ).toBeVisible();
    }

    // On mobile a reload IS the return path, so without the cap this would come back on every page
    // view for the whole freshness window (§6.3).
    await arm(authedPage, anchor);
    await returnAfter(authedPage, 25_000);
    await expect(authedPage.getByText("Compare this story")).toBeHidden();
  });

  test("a read past the freshness window is not offered", async ({ authedPage }) => {
    const anchor = ANCHOR_URL;
    seedAnchor(anchor);
    await authedPage.goto("/discover", { waitUntil: "networkidle" });
    await arm(authedPage, anchor, 5 * 60 * 60 * 1000); // 5 h ago, past the 4 h window
    await returnAfter(authedPage, 25_000);
    await expect(authedPage.getByText("Compare this story")).toBeHidden();
  });

  test("fires when the tab went hidden BEFORE the candidate armed", async ({ authedPage }) => {
    // The real production ordering, and a bug that made the strip never appear at all. The Read
    // click starts the prefetch and calls window.open on the SAME tick, so the tab is hidden while
    // the request is still in flight. The candidate arms only when it resolves — strictly after the
    // `hidden` event — so the card enables its listener having already missed it, and the return
    // reads as a visible-without-a-preceding-hide, which the gate correctly and uselessly ignores.
    //
    // Driven through the real ReadArticleButton with the response DELAYED, because that ordering is
    // the whole point: an earlier version of this test armed via sessionStorage + reload, which
    // attaches the listener while visible and therefore passed with or without the fix.
    const anchor = ANCHOR_URL;
    seedAnchor(anchor);

    let release: (() => void) | null = null;
    const armed = new Promise<void>((r) => (release = r));
    await authedPage.route("**/api/me/continuation*", async (route) => {
      await armed;                                  // hold the prefetch until the tab is hidden
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...OFFER, anchor: { ...OFFER.anchor, url: anchor } }),
      });
    });

    await authedPage.goto("/discover", { waitUntil: "networkidle" });
    const card = authedPage
      .locator("article")
      .filter({ hasText: "Regulator publishes the annual spectrum auction timetable" })
      .first();
    await expect(card).toBeVisible();

    // window.open spawns a popup Playwright would otherwise wait on; close it as it appears.
    authedPage.context().on("page", (p) => void p.close().catch(() => {}));
    await card.getByRole("button", { name: /Read article/i }).click();

    await goHidden(authedPage);                     // the tab leaves while the prefetch is in flight
    release!();                                     // …and only NOW does the candidate arm
    await authedPage.waitForFunction(
      (k) => window.sessionStorage.getItem(k as string) !== null,
      ARMED_KEY,
    );

    await comeBack(authedPage, 25_000);
    await expect(authedPage.getByText("Compare this story")).toBeVisible();
  });

  test("also fires on the story page's coverage list", async ({ authedPage }) => {
    // The surface with the best odds by construction: every row in a story's coverage list is
    // already a cluster member, so the membership gate that rejects ~4 in 5 Discover cards passes
    // automatically. The "all outlets" link is suppressed here — it would point at this very page.
    seedStoryCluster();
    await authedPage.goto("/stories", { waitUntil: "networkidle" });

    // Resolve the story by ID rather than clicking it out of the list. /stories is RANKED (trusted,
    // then publisherCount, then coverage), so a two-publisher fixture sinks as other specs seed
    // into the shared catalog — this test passed alone and failed in the full suite for exactly
    // that reason, which is a fact about the ranking and not about the strip.
    const storyId = await authedPage.evaluate(async () => {
      const res = await fetch("/api/stories?limit=200");
      const body = (await res.json()) as { stories?: { id: string; title: string }[] };
      return (body.stories ?? []).find((x) => (x.title ?? "").includes("Levee inspection board"))
        ?.id ?? null;
    });
    expect(storyId, "the seeded pair must cluster into a story").not.toBeNull();

    await authedPage.goto(`/stories/${storyId}`, { waitUntil: "networkidle" });
    await expect(authedPage.getByText("CBS News").first()).toBeVisible();

    await arm(authedPage, STORY_ANCHOR);
    await returnAfter(authedPage, 25_000);

    await expect(authedPage.getByText("Compare this story")).toBeVisible();
    await expect(authedPage.getByRole("button", { name: "Read another perspective" })).toBeVisible();
    // …and no self-referential link back to the page the reader is already on.
    await expect(authedPage.getByRole("link", { name: /View all \d+ outlets/ })).toHaveCount(0);
  });
});