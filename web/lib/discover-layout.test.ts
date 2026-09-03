// The card-stream layout (2026-08-23): a uniform grid over uniform cards.
//
// History, so the pins make sense: the uniform grid originally failed because the cards feeding
// it were bimodal — image cards ran ~2.3× text-first cards, and grid stretch rendered the
// difference as dead space. A masonry interlude compensated in placement; the real fix landed in
// the CARD — an always-occupied image slot (publisher placeholder when art is absent,
// engine-flagged branding, or broken) and one type scale — after which the masonry machinery was
// retired and the grid returned, keeping exact row-major reading order and DOM order matching
// visual order.
//
// These are source pins, the house dialect for visual regressions (see core-import-guard.test.ts):
// each asserts a structural fact the layout depends on, so the failure names the law it broke.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const WEB = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(WEB, p), "utf8");
// Three columns from lg, not xl (desktop rework, docs/DESKTOP_EDITORIAL_AUDIT.md part 2): with
// the sidebar gone the content column is 960px at 1024, which holds three ~300px cards — the
// density of a news product's desktop feed — where two 460px cards read as a tablet layout.
const GRID = "grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3";

test("every card stream renders the same responsive grid: 1 column, md 2, lg 3", () => {
  for (const page of [
    "app/(app)/discover/page.tsx",
    "app/(app)/search/page.tsx",
    "app/(app)/saved/page.tsx",
  ]) {
    const src = read(page);
    assert.ok(src.includes(GRID), `${page} must lay its cards on the shared grid scale`);
    assert.ok(src.includes("<DiscoverCard"), `${page} renders DiscoverCard`);
  }
});

test("the masonry machinery stays retired", () => {
  // The card's occupied image slot made heights near-uniform; placement compensation on top of
  // it would be dead architecture. If height variance ever returns, fix the CARD, not the grid.
  for (const gone of [
    "components/shared/masonry-columns.tsx",
    "lib/masonry-order.ts",
    "lib/discover-card-height.ts",
  ]) {
    assert.ok(!existsSync(join(WEB, gone)), `${gone} was deleted and must stay deleted`);
  }
  for (const page of [
    "app/(app)/discover/page.tsx",
    "app/(app)/search/page.tsx",
    "app/(app)/saved/page.tsx",
  ]) {
    assert.ok(!read(page).includes("MasonryColumns"), `${page} must not resurrect masonry`);
  }
});

test("every card leads with an occupied image slot — art or the publisher plate", () => {
  const slot = read("components/shared/article-image-slot.tsx");
  assert.ok(slot.includes("<ArticleImage"), "the slot renders article art when usable");
  assert.ok(slot.includes("<PublisherLogo"), "and the publisher's mark otherwise");
  assert.ok(slot.includes("article.imageSuspect"), "engine-flagged branding never fronts as art");
  assert.ok(
    slot.includes('aria-hidden="true"'),
    "the placeholder is decorative — the metadata row names the publisher",
  );
  // The plate, not the void (2026-08-30): identity-derived colour from the shared core rule, so
  // the same outlet is tinted identically on every surface — and the dimmed-grey treatment that
  // read as a broken image beside real photos stays dead.
  assert.ok(
    slot.includes("placeholderHues(article.publisher"),
    "the plate's colour must derive from the publisher via @ih/core/logic/placeholder-art",
  );
  assert.ok(
    !slot.includes("grayscale") && !slot.includes("opacity-35"),
    "the dimmed-grey placeholder stays retired — the plate renders the mark in full colour",
  );
  const card = read("components/discover/discover-card.tsx");
  const h3 = card.lastIndexOf("<h3");
  const use = card.lastIndexOf("<ArticleImageSlot");
  assert.ok(use >= 0 && use < h3, "DiscoverCard leads with the shared slot");
  assert.ok(
    !card.includes("line-clamp-6") && !card.includes("text-lg"),
    "senior-type compensation stays retired: the occupied slot carries the rhythm, one type scale",
  );
});

test("the recommendation card reuses the SAME slot — one fallback implementation, no copies", () => {
  const rec = read("components/recommendations/recommendation-card.tsx");
  assert.ok(
    rec.includes("<ArticleImageSlot article={article}"),
    "recommendation cards front the shared slot instead of voiding when art is missing",
  );
  for (const consumer of [
    "components/discover/discover-card.tsx",
    "components/recommendations/recommendation-card.tsx",
  ]) {
    const src = read(consumer);
    assert.ok(
      !src.includes("<PublisherLogo") && !src.includes("<ArticleImage "),
      `${consumer} must not carry its own copy of the slot's internals`,
    );
  }
});

test("the card flows image slot → headline → metadata → summary → slack → actions", () => {
  const card = read("components/discover/discover-card.tsx");
  const at = (needle: string) => {
    const i = card.lastIndexOf(needle);
    assert.ok(i >= 0, `card must contain ${needle}`);
    return i;
  };
  const slot = at("<ArticleImageSlot");
  const headline = at("<h3");
  const metadata = at("<PublisherBadge");
  const summary = at("article.description");
  const slack = at('className="flex-1"');
  const lean = at("<LeanBadge");
  const actions = at("<ReadArticleButton");
  assert.ok(slot < headline, "image slot precedes headline");
  assert.ok(headline < metadata, "headline precedes metadata");
  assert.ok(metadata < summary, "metadata precedes summary");
  assert.ok(
    summary < slack && slack < lean && lean < actions,
    "the single slack point sits between summary and footer, so grid stretch vanishes there " +
      "and action rows sit flush across a row",
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
