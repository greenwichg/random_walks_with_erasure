// Discover's variable-height layout (2026-08-23, Flipboard-inspired in flow, not copied in UI).
//
// The uniform grid stretched every card in a row to the row's tallest card, and the card's
// internal flex stretcher rendered the difference as a void between summary and badges. The
// stream now renders through MasonryColumns — natural heights, row-major reading order,
// append-only Load More — and the card flows image → headline → metadata → summary → actions.
//
// Two layers of pins:
//   1. The distribution LAW (lib/masonry-order.ts), as behavior: reading order and append
//      stability are properties the reader experiences, so they are asserted, not described.
//   2. The layout WIRING, as source pins (the house dialect for visual regressions — see
//      core-import-guard.test.ts): Discover renders cards through MasonryColumns, the card
//      keeps the agreed flow, the stretcher stays gone, and the recommendation-side laws the
//      layout must not touch (publisher interleave, lean-said-once) stay in place.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  MASONRY_BREAKPOINTS,
  MASONRY_DEFAULT_COUNT,
  distributeByHeight,
  distributeIndexes,
} from "./masonry-order.ts";
import { estimateDiscoverCardHeight } from "./discover-card-height.ts";

// The height shape that broke count-based round-robin in production (2026-08-23: one column
// ended thousands of px before its neighbor). The card's always-occupied image slot has since
// removed this bimodality from Discover itself, but the placement algorithm must bound ANY
// input — this stays as its stress case.
const BIMODAL = Array.from({ length: 48 }, (_, i) =>
  i % 5 === 0 || i < 10 ? 240 : 560,
);

const WEB = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(WEB, p), "utf8");

test("round-robin columns read row-major: column tops reconstruct the input order", () => {
  for (const count of [1, 2, 3]) {
    const columns = distributeIndexes(10, count);
    const rows: number[] = [];
    const tallest = Math.max(...columns.map((c) => c.length));
    for (let r = 0; r < tallest; r += 1)
      for (const col of columns) if (col[r] !== undefined) rows.push(col[r]!);
    assert.deepEqual(rows, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], `count=${count}`);
  }
});

test("Load More is append-only: a longer list never moves an earlier card", () => {
  // One page (24) then two (48), at every column count the breakpoints can produce: every
  // column of the shorter distribution must be an exact prefix of the longer one's.
  for (const count of [MASONRY_DEFAULT_COUNT, ...MASONRY_BREAKPOINTS.map((b) => b.count)]) {
    const before = distributeIndexes(24, count);
    const after = distributeIndexes(48, count);
    before.forEach((col, c) => {
      assert.deepEqual(after[c]!.slice(0, col.length), col, `count=${count} col=${c}`);
    });
  }
});

test("height-aware placement keeps the append law: a longer list never moves an earlier card", () => {
  for (const count of [MASONRY_DEFAULT_COUNT, ...MASONRY_BREAKPOINTS.map((b) => b.count)]) {
    const before = distributeByHeight(24, count, (i) => BIMODAL[i]!);
    const after = distributeByHeight(48, count, (i) => BIMODAL[i]!);
    before.forEach((col, c) => {
      assert.deepEqual(after[c]!.slice(0, col.length), col, `count=${count} col=${c}`);
    });
  }
});

test("height-aware placement bounds the column skew at one card — the production failure", () => {
  // The void in the 2026-08-23 screenshot: round-robin's skew on a bimodal stream exceeds a
  // whole card, and nothing bounds it. Shortest-column placement is bounded by construction.
  for (const count of [2, 3]) {
    const fill = (columns: number[][]) =>
      columns.map((col) => col.reduce((h, i) => h + BIMODAL[i]!, 0));
    const greedy = fill(distributeByHeight(48, count, (i) => BIMODAL[i]!));
    const maxItem = Math.max(...BIMODAL);
    assert.ok(
      Math.max(...greedy) - Math.min(...greedy) <= maxItem,
      `count=${count}: skew ${Math.max(...greedy) - Math.min(...greedy)} exceeds one card`,
    );
  }
});

test("height-aware columns stay chronological top to bottom", () => {
  for (const count of [1, 2, 3]) {
    for (const col of distributeByHeight(48, count, (i) => BIMODAL[i]!)) {
      const sorted = [...col].sort((a, b) => a - b);
      assert.deepEqual(col, sorted, `count=${count}`);
    }
  }
});

test("the card-height estimate mirrors the card's own rules: one rhythm, text-only variance", () => {
  const short = { headline: "Brief headline", description: "" };
  const long = {
    headline:
      "A very long headline that will wrap over several rendered lines on a desktop column width",
    description:
      "A summary long enough to exhaust the three-line clamp when the card renders it at the " +
      "typical column width, with room to spare beyond the clamp boundary for good measure.",
  };
  // The slot is ALWAYS occupied (art or publisher placeholder), so image fields must be
  // irrelevant to the estimate — art and placeholder fill the same slot.
  const withImage: { headline: string; description?: string | null } = {
    ...long,
    ...( { image: "https://example.com/a.jpg", imageSuspect: false } as object),
  };
  assert.equal(estimateDiscoverCardHeight(withImage), estimateDiscoverCardHeight(long));
  const longer = { ...long, description: `${long.description} ${long.description}` };
  assert.ok(
    estimateDiscoverCardHeight(longer) >= estimateDiscoverCardHeight(long),
    "more summary never shrinks the estimate",
  );
  // Uniform rhythm: with the slot constant and the clamps bounded, the tallest and shortest
  // possible cards differ by at most the text band (3 extra headline lines + 3 summary lines).
  const band = estimateDiscoverCardHeight(long) - estimateDiscoverCardHeight(short);
  assert.ok(band > 0 && band <= 3 * 24 + 3 * 20 + 8, `band=${band}`);
});

test("every card leads with an occupied image slot — art or the publisher placeholder", () => {
  const card = read("components/discover/discover-card.tsx");
  const h3 = card.lastIndexOf("<h3");
  const art = card.lastIndexOf("<ArticleImage");
  const placeholder = card.lastIndexOf("<PublisherLogo");
  assert.ok(art >= 0 && art < h3, "article art leads when present");
  assert.ok(placeholder >= 0 && placeholder < h3, "the publisher placeholder fills the slot otherwise");
  assert.ok(
    card.includes('aria-hidden="true"'),
    "the placeholder is decorative — the metadata row names the publisher",
  );
  assert.ok(
    !card.includes("line-clamp-6") && !card.includes("text-lg"),
    "senior-type compensation is retired: the occupied slot carries the rhythm, one type scale",
  );
});

test("the responsive scale mirrors the app's grid: 1 column, md 2, xl 3", () => {
  assert.equal(MASONRY_DEFAULT_COUNT, 1);
  assert.deepEqual(
    MASONRY_BREAKPOINTS.map((b) => [b.query, b.count]),
    [
      ["(min-width: 1280px)", 3],
      ["(min-width: 768px)", 2],
    ],
  );
});

test("Discover renders the stream through MasonryColumns, not a stretch grid", () => {
  const page = read("app/(app)/discover/page.tsx");
  assert.ok(page.includes("<MasonryColumns"), "the stream must render through MasonryColumns");
  assert.ok(page.includes("<DiscoverCard"), "MasonryColumns must render DiscoverCard");
  assert.ok(
    page.includes("estimateHeight={estimateDiscoverCardHeight}"),
    "Discover's bimodal cards need height-aware placement, not count-based round-robin",
  );
  // The only grid left on the page is the loading skeleton; the card stream itself must never
  // regain a row-stretching wrapper.
  const grids = page.match(/grid-cols-1/g) ?? [];
  assert.equal(grids.length, 1, "exactly one grid remains (the skeleton), none around the cards");
  assert.ok(
    page.includes("items-start"),
    "the skeleton grid is top-aligned so it previews natural heights",
  );
});

test("the layout change did not touch the recommendation-side laws", () => {
  const page = read("app/(app)/discover/page.tsx");
  assert.ok(
    page.includes("interleavePublishers(articles)"),
    "publisher interleave still orders the stream",
  );
  assert.ok(page.includes("leanDot={false}"), "lean-said-once holds on Discover");
});

test("the card flows image → headline → metadata → summary → actions, with no stretcher", () => {
  const card = read("components/discover/discover-card.tsx");
  const at = (needle: string) => {
    const i = card.lastIndexOf(needle);
    assert.ok(i >= 0, `card must contain ${needle}`);
    return i;
  };
  const image = at("<ArticleImage");
  const headline = at("<h3");
  const metadata = at("<PublisherBadge");
  const summary = at("article.description");
  const lean = at("<LeanBadge");
  const actions = at("<ReadArticleButton");
  assert.ok(image < headline, "image precedes headline");
  assert.ok(headline < metadata, "headline precedes metadata");
  assert.ok(metadata < summary, "metadata precedes summary");
  assert.ok(summary < lean && lean < actions, "summary precedes badges and actions");
  assert.ok(
    !card.includes('className="flex-1"'),
    "no internal stretcher — a short card ends where its content ends",
  );
});
