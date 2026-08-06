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

/**
 * The `continuation_suppressed` reasons the ENGINE has stored for this reader.
 *
 * Asserted at the store rather than on the wire, for two reasons. The provider ships batches via
 * `navigator.sendBeacon`, and Playwright exposes no body for those — `postData()` and
 * `postDataBuffer()` both return null, which reads as "an empty batch" and is indistinguishable
 * from the event never firing. And the store is the honest end of the contract: an event that
 * reaches `/api/events` but is not in `product_analytics.EVENTS` is DROPPED, which is exactly how
 * all six continuation events were lost for the feature's whole life. This asserts it survived.
 */
function suppressedReasons(userId: number): string[] {
  const py = `
import json, sys
sys.path.insert(0, ${JSON.stringify(path.join(REPO_ROOT, "examples"))})
import store as store_mod

st = store_mod.Store("sqlite:///" + ${JSON.stringify(DB)})
out = [ (r.get("props") or {}).get("reason")
        for r in st.list_analytics_events()
        if r.get("event") == "continuation_suppressed" and r.get("userId") == ${userId} ]
print(json.dumps(out))
`;
  return JSON.parse(execSync("python3 -", { input: py, encoding: "utf8" }).trim());
}

/** Put the OFFERED article in the catalog too, so a real Discover card carries its url and the
 *  sibling can be read through the same button every surface uses. */
function seedSibling(url: string, headline: string): void {
  const py = `
import sys
sys.path.insert(0, ${JSON.stringify(path.join(REPO_ROOT, "examples"))})
import ingest
import store as store_mod

st = store_mod.Store("sqlite:///" + ${JSON.stringify(DB)})
url = ${JSON.stringify(url)}
title = ${JSON.stringify(headline)}
st.upsert_feed_article(
    canonical_url=ingest.canonical_url(url), url=url, publisher="Fox News",
    source_publisher=None, title=title,
    description="The regulator's timetable drew responses across the sector. " * 3,
    body=None, published_at="2026-08-03T09:00:00Z", source_feed="e2e",
    scored={"article_id": url, "outlet": "Fox News", "category": "Politics",
            "lean": 2.0, "political": True, "title": title})
print("seeded sibling")
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

  test("stops after two impressions without engagement", async ({ authedPage, uid }) => {
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
    //
    // The capped return must also REPORT itself. A qualifying return that renders nothing is
    // indistinguishable, from outside the browser, from a return that was never detected — and the
    // two have different causes. Production hit exactly that ambiguity: armed 6, shown 1, and no
    // way to tell which of the two had happened.
    await arm(authedPage, anchor);
    await returnAfter(authedPage, 25_000);
    await expect(authedPage.getByText("Compare this story")).toBeHidden();
    await expect
      .poll(() => suppressedReasons(uid), { timeout: 15_000, intervals: [500, 1000, 2000] })
      .toEqual(["capped"]);
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

  // ------------------------------------------------------------------ Recommendations (primary)
  //
  // Design §9.1.1: Recommendations is the PRIMARY surface. It is also the only one with a failure
  // mode of its own, and `be0426d` fixed it without a regression test — this is that test.
  //
  // The feed is stubbed rather than driven off the engine, because the defect is entirely
  // client-side and the engine's half of it is a fact this spec would only be re-stating: the
  // recommender excludes articles the reader has read (`exclude_seen=True`), so the fetch that
  // follows a read comes back WITHOUT the article just opened. The stub reproduces exactly that —
  // the anchor is in the first response and absent from every later one — so a refetch during the
  // reader's absence unmounts the card, taking its ContinuationStrip with it.

  const REC_ANCHOR = "https://cbs-rec.example.com/e2e/tariff";
  const REC_OTHER = "https://npr-rec.example.com/e2e/ferry";

  function recFor(url: string, headline: string, publisher: string) {
    return {
      article: {
        id: url,
        headline,
        publisher,
        topic: "Politics",
        url,
        lean: -1,
        leanBucket: "left",
        publishedAt: "2026-08-03T12:00:00Z",
        readingMinutes: 4,
      },
      reason: "Broadens the outlets you read.",
      strategy: "rwe-b",
      helpsMetric: "sourceDiversity",
      crossCutting: true,
    };
  }

  /** Serve a two-card feed, then a one-card feed — the engine's own post-read behaviour. Returns a
   *  counter so the test can assert on the REFETCH, which is the thing the fix suppresses. */
  async function stubFeed(page: import("@playwright/test").Page): Promise<{ n: number }> {
    const calls = { n: 0 };
    await page.route(
      (url) => url.pathname === "/api/recommendations",
      async (route) => {
        calls.n += 1;
        const feed =
          calls.n === 1
            ? [
                recFor(REC_ANCHOR, "Trade panel reopens the tariff schedule for review", "CBS News"),
                recFor(REC_OTHER, "Ferry operator publishes its winter timetable", "NPR"),
              ]
            : [recFor(REC_OTHER, "Ferry operator publishes its winter timetable", "NPR")];
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(feed),
        });
      },
    );
    return calls;
  }

  /** Answer the prefetch with a real offer for `anchor`, optionally after `delayMs`. */
  async function stubContinuation(
    page: import("@playwright/test").Page,
    anchor: string,
    delayMs = 0,
  ): Promise<void> {
    await page.route("**/api/me/continuation*", async (route) => {
      if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...OFFER, anchor: { ...OFFER.anchor, url: anchor } }),
      });
    });
  }

  test("survives the read-invalidation on Recommendations", async ({ authedPage }) => {
    const feed = await stubFeed(authedPage);
    await stubContinuation(authedPage, REC_ANCHOR);
    authedPage.context().on("page", (p) => void p.close().catch(() => {}));

    await authedPage.goto("/recommendations", { waitUntil: "networkidle" });
    const card = authedPage
      .locator("article")
      .filter({ hasText: "Trade panel reopens the tariff schedule" })
      .first();
    await expect(card).toBeVisible();
    expect(feed.n).toBe(1);

    await card.getByRole("button", { name: /Read article/i }).click();
    await authedPage.waitForFunction(
      (k) => window.sessionStorage.getItem(k as string) !== null,
      ARMED_KEY,
    );
    await goHidden(authedPage);
    // Past the 700 ms beacon grace in useRecordRead, so onSettled has definitely run and its
    // invalidation has had every chance to evict the card.
    await authedPage.waitForTimeout(1_500);

    await comeBack(authedPage, 25_000);
    expect(feed.n, "an armed continuation must not be refetched out from under").toBe(1);
    await expect(card).toBeVisible();
    await expect(authedPage.getByText("Compare this story")).toBeVisible();
  });

  test("a read with NO continuation still refetches the feed immediately", async ({ authedPage }) => {
    // The other half of the fix, and the one a careless version would break: the 2026-08-02
    // read-invalidation must stay immediate for the ~95% of reads the engine declines. Holding the
    // feed back for every read would trade one bug for a staler feed on every read.
    const feed = await stubFeed(authedPage);
    await authedPage.route("**/api/me/continuation*", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "null" }),
    );
    authedPage.context().on("page", (p) => void p.close().catch(() => {}));

    await authedPage.goto("/recommendations", { waitUntil: "networkidle" });
    const card = authedPage
      .locator("article")
      .filter({ hasText: "Trade panel reopens the tariff schedule" })
      .first();
    await expect(card).toBeVisible();

    await card.getByRole("button", { name: /Read article/i }).click();
    await expect
      .poll(() => feed.n, { timeout: 10_000, message: "the feed must refresh after an ordinary read" })
      .toBe(2);
  });

  // ---------------------------------------------------------------- the feed instance (§9.1.2)
  test("appears in Recommendations after a read that happened somewhere else", async ({
    authedPage,
  }) => {
    // The case that failed in production for weeks and that no card-bound instance can serve: the
    // reader opens something on Discover, comes back, and goes to Recommendations. There has never
    // been a Recommendations card for that article — and after the read there cannot be, because
    // the recommender excludes what has been read. Nothing was mounted to render the offer.
    const anchor = ANCHOR_URL;
    seedAnchor(anchor);
    await stubFeed(authedPage);

    await authedPage.goto("/discover", { waitUntil: "networkidle" });
    await arm(authedPage, anchor, 30_000);        // read 30 s ago: past the 20 s dwell equivalent

    // Navigate to Recommendations WITHOUT any visibility transition. There is no hide for a
    // listener to observe here, which is precisely why the card-bound trigger cannot fire.
    await authedPage.getByRole("link", { name: "Recommendations", exact: true }).first().click();

    await expect(authedPage.getByText("Compare this story")).toBeVisible();
    await expect(authedPage.getByText(/Fox News is rated right of centre/)).toBeVisible();

    // The feed instance carries what a CARD carries, because it sits among cards and a reader
    // deciding whether to click needs the same evidence: the sibling's own headline (which the
    // strip did not show at all at first), its publisher as a linked chip rather than a name buried
    // in a sentence, and its lean as a badge.
    await expect(
      authedPage.getByRole("heading", { name: "Regulator sets out the auction timetable" }),
    ).toBeVisible();
    await expect(authedPage.getByRole("link", { name: "Fox News" })).toBeVisible();
  });

  test("a read younger than the dwell window is not offered yet, then is", async ({ authedPage }) => {
    // Time since the read replaces the dwell gate on this surface, so it has to hold the same line:
    // a reader who clicks Read and bounces straight to the feed has not been anywhere.
    const anchor = ANCHOR_URL;
    seedAnchor(anchor);
    await stubFeed(authedPage);

    await authedPage.goto("/recommendations", { waitUntil: "networkidle" });
    await arm(authedPage, anchor, 2_000);         // 2 s ago
    await expect(authedPage.getByText("Compare this story")).toBeHidden();

    // …and it arrives on its own once the window passes, without another navigation.
    await expect(authedPage.getByText("Compare this story")).toBeVisible({ timeout: 25_000 });
  });

  test("one story is not offered twice — the feed's own story card stands down", async ({
    authedPage,
  }) => {
    // Both the strip and the engine's story-match card say "another outlet covered this". Showing
    // both for ONE story is the same offer twice in different words; the strip is the more specific
    // and wins while it is up.
    const anchor = ANCHOR_URL;
    seedAnchor(anchor);
    await authedPage.route(
      (url) => url.pathname === "/api/recommendations",
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              ...recFor(REC_OTHER, "Ferry operator publishes its winter timetable", "NPR"),
              strategy: "story",
              explanation: {
                type: "story_match",
                variant: "same_event",
                message: "Another outlet covered this.",
                evidence: { storyId: OFFER.storyId, readPublisher: "CBS News", recPublisher: "NPR" },
              },
            },
          ]),
        }),
    );

    await authedPage.goto("/recommendations", { waitUntil: "networkidle" });
    await expect(authedPage.getByText("Ferry operator publishes its winter timetable")).toBeVisible();

    await arm(authedPage, anchor, 30_000);
    await expect(authedPage.getByText("Compare this story")).toBeVisible();
    // …and the engine's card for the SAME story is gone while the strip is up.
    await expect(authedPage.getByText("Ferry operator publishes its winter timetable")).toBeHidden();
  });

  test("fires on a card after a RELOAD, with no visibility transition at all", async ({
    authedPage,
  }) => {
    // The mobile path, and the reason this did not work on a phone. `window.open` backgrounds the
    // tab hardest there, and a backgrounded tab is routinely DISCARDED — so coming back reloads the
    // page. A fresh document starts visible and fires no hidden→visible pair, which leaves a
    // visibility-only trigger structurally unable to ever fire on that platform.
    //
    // sessionStorage survives the reload (§6.2 chose it for exactly this), so the offer is still
    // armed and merely had nothing left to trigger it. `arm()` already reloads, so this test is the
    // mobile sequence verbatim: arm, reload, no goHidden/comeBack anywhere.
    const anchor = ANCHOR_URL;
    seedAnchor(anchor);
    await authedPage.goto("/discover", { waitUntil: "networkidle" });
    await arm(authedPage, anchor, 30_000);        // read 30 s ago, then the page came back fresh

    await expect(authedPage.getByText("Compare this story")).toBeVisible();
  });

  test("a visibility return and a fresh mount do not both count an impression", async ({
    authedPage,
  }) => {
    // Two triggers now reach the same offer. If they could both fire, one read would burn both
    // impressions at once and the second return would be silently capped — the cap arriving early
    // and looking exactly like the bug this whole thread has been chasing.
    const anchor = ANCHOR_URL;
    seedAnchor(anchor);
    await authedPage.goto("/discover", { waitUntil: "networkidle" });
    await arm(authedPage, anchor, 30_000);
    await expect(authedPage.getByText("Compare this story")).toBeVisible();

    await returnAfter(authedPage, 25_000);        // …and now a real visibility return as well
    await expect(authedPage.getByText("Compare this story")).toBeVisible();

    const n = await authedPage.evaluate(
      ([k, id]) => JSON.parse(window.localStorage.getItem(k as string) ?? "{}")[id as string]?.n,
      [STATE_KEY, OFFER.storyId] as const,
    );
    expect(n, "one offer, one impression — not one per trigger").toBe(1);
  });

  test("reading the offered sibling from elsewhere retires the strip", async ({ authedPage }) => {
    // §1.4: "sibling read in the meantime — derived from live read state, not a snapshot". The
    // armed candidate IS a snapshot taken at the anchor's click, and it survives reloads by design,
    // so without this the strip comes back after a refresh still offering an article the reader has
    // already read.
    //
    // Driven through the REAL Read button on the sibling's own card, because the wiring under test
    // is `useRecordRead` — the one mutation every surface shares. A unit test on
    // `retireIfSiblingRead` cannot tell whether anything calls it.
    seedSibling(OFFER.sibling.url, OFFER.sibling.headline);
    await authedPage.goto("/discover", { waitUntil: "networkidle" });
    await arm(authedPage, ANCHOR_URL, 30_000);
    await expect(authedPage.getByText("Compare this story")).toBeVisible();

    authedPage.context().on("page", (p) => void p.close().catch(() => {}));
    const siblingCard = authedPage
      .locator("article")
      .filter({ hasText: OFFER.sibling.headline })
      .first();
    await expect(siblingCard).toBeVisible();
    await siblingCard.getByRole("button", { name: /Read article/i }).click();

    // Gone at once, and still gone after a reload — the snapshot in sessionStorage is retired, not
    // merely hidden in this render.
    await expect(authedPage.getByText("Compare this story")).toBeHidden();
    await authedPage.reload({ waitUntil: "networkidle" });
    await expect(authedPage.getByText("Compare this story")).toBeHidden();
  });
});
