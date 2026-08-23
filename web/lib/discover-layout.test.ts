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
  distributeIndexes,
} from "./masonry-order.ts";

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
