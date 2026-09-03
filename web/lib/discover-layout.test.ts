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

test("every card leads with an occupied image slot — art or the shared newspaper fallback", () => {
  const slot = read("components/shared/article-image-slot.tsx");
  assert.ok(slot.includes("<CardImage"), "the article slot delegates to the one shared slot");
  assert.ok(
    slot.includes("suspect={article.imageSuspect}"),
    "engine-flagged branding never fronts as art — it takes the fallback like an absent image",
  );
  // The publisher plate is retired (the fallback brief): one image for every card in the app, so
  // an article card and a story card with the same problem no longer look like two products.
  assert.ok(
    !slot.includes("placeholderHues") && !slot.includes("<PublisherLogo"),
    "the per-publisher plate stays retired — the outlet is named by the metadata row instead",
  );
  const image = read("components/shared/card-image.tsx");
  assert.ok(
    image.includes("<StoryFallbackArt"),
    "CardImage falls back to the shared art rather than rendering nothing",
  );
  assert.ok(
    image.includes('aria-hidden="true"'),
    "the fallback is decorative — it carries no fact the card does not already state in text",
  );
  assert.ok(
    !image.includes("return null"),
    "the slot is never empty: a void in a grid row is the thing this component exists to end",
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

/**
 * ONE fallback, every card surface. The requirement was "apply this consistently across the entire
 * app", and the failure mode it replaced was ten surfaces each deciding for themselves: four
 * rendered a plate, four rendered literally nothing (holes down a thumbnail column), two differed
 * again. Anything that fronts a story or article card goes through CardImage, and no surface is
 * allowed to reintroduce its own `image ? … : …` branch.
 */
test("every story surface fronts the ONE shared slot — no per-surface fallback, no voids", () => {
  const surfaces = [
    "components/stories/story-card.tsx",
    "components/shared/lead-story.tsx",
    "components/shared/spot-card.tsx",
    "components/shared/story-row.tsx",
    "components/home/story-list-item.tsx",
    "components/home/hero-story.tsx",
    "components/home/story-feature-card.tsx",
    "components/home/desktop/home-desktop.tsx",
  ];
  for (const surface of surfaces) {
    const src = read(surface);
    assert.ok(src.includes("<CardImage"), `${surface} must front the shared slot`);
    assert.ok(
      !src.includes("<CoveragePlate"),
      `${surface} must not re-introduce the plate as a card fallback`,
    );
    // The old `story.image ? … : …` / `&& story.image` guards are exactly what left the voids.
    assert.ok(
      !/\{\s*story\.image\s*(\?|&&)/.test(src) && !/\{\s*lead\.image\s*(\?|&&)/.test(src),
      `${surface} must not branch on the image itself — CardImage owns that decision`,
    );
  }
  // The fallback art itself: house-drawn, theme-aware, and clear of the lean axis, with the
  // licensed-photo swap point kept to a single constant.
  const art = read("components/shared/story-fallback-art.tsx");
  assert.ok(art.includes("FALLBACK_PHOTO_SRC"), "the licensed-photo swap point stays a one-liner");
  assert.ok(
    art.includes("PLACEHOLDER_HUES"),
    "accents come from the curated wheel, which is what keeps them off the lean axis",
  );
  assert.ok(
    !/hsl\(\s*(214|356)\b/.test(art),
    "no left-blue or right-red in art repeated on every imageless card — it would read as politics",
  );
  assert.ok(
    art.includes("hsl(var(--card))") && art.includes("hsl(var(--foreground)"),
    "the art is drawn in theme tokens, so dark mode is not a bright rectangle in a charcoal grid",
  );
  assert.ok(
    art.includes('preserveAspectRatio="xMidYMid slice"'),
    "the drawing crops like the photograph it stands in for (slice === object-fit: cover)",
  );
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
