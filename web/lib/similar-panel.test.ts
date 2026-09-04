// The Similar Stories rail card, checked where it is cheapest to check: against the card it
// replaces and against the rules that keep it honest.
//
// It has no logic of its own to unit-test — the stories are the engine's ranked answer, handed in
// by the page. What CAN rot is what this file asserts: that the card still wears the shell "Picked
// for you" established, that it never grows a similarity opinion, that it still tells loading,
// failure and genuine absence apart, and that it stays on the viewport it was specified for. That
// last one is the reason the horizontal rail is still in the tree: the card replaces it on DESKTOP
// only, and below `lg` the page is the one it always was.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const WEB = join(import.meta.dirname, "..");
const PANEL = readFileSync(join(WEB, "components", "stories", "similar-stories-panel.tsx"), "utf8");
const REFERENCE = readFileSync(join(WEB, "components", "home", "recommendation-panel.tsx"), "utf8");
const PAGE = readFileSync(join(WEB, "app", "(app)", "stories", "[id]", "page.tsx"), "utf8");
const RAIL = readFileSync(join(WEB, "components", "stories", "similar-stories.tsx"), "utf8");

/** The shell the reference established: container, header treatment, row rhythm. */
const SHELL = [
  'className="rounded-lg border bg-card p-4"',   // the card itself
  "<SectionHeader",                              // the header, not a hand-rolled one
  '"home.viewAll"',                              // the "View all" treatment
  "actionLabel=",
  'className="mb-3"',
  '<ul className="divide-y">',                   // the dividers
  'className="group block rounded-md py-3',      // the row
  'className="mb-1 flex flex-wrap items-center gap-2"',
  'className="text-xs font-medium text-muted-foreground"',                 // publisher eyebrow
  'className="line-clamp-2 text-sm font-semibold leading-snug tracking-tight',  // headline
  'className="mt-1.5 line-clamp-2 text-xs text-muted-foreground"',          // description
  'className="mt-1.5 inline-flex items-center gap-1 text-[0.68rem] font-medium text-primary/80"',
];

test("the card wears the shell the reference established", () => {
  for (const marker of SHELL) {
    assert.ok(REFERENCE.includes(marker), `the reference no longer has ${marker} — update this list`);
    assert.ok(PANEL.includes(marker), `Similar Stories has drifted from the reference shell: ${marker}`);
  }
});

test("it holds no opinion about what similar means", () => {
  // The stories arrive as a prop. A fetch, a sort or a score here would be a second matching rule
  // to disagree with the engine's — which is the defect the rail was originally reported for.
  assert.ok(/similar: Story\[\]/.test(PANEL), "the ranked answer must be passed in, not fetched");
  for (const forbidden of ["useSimilarStories", "useQuery", "fetch(", ".sort(", "score"]) {
    assert.ok(!PANEL.includes(forbidden), `Similar Stories panel must not ${forbidden}`);
  }
});

test("the story page shows it instead of Picked for you — on desktop", () => {
  assert.ok(PAGE.includes("<SimilarStoriesPanel"), "the card is not on the story page");
  assert.ok(/similar=\{related\}/.test(PAGE), "the card must be fed the engine's ranked answer");
  // The swap was specified for the desktop story view, so it is made by mounting one composition
  // or the other — not by a CSS class that hides a mounted tree, which would run both queries.
  assert.ok(PAGE.includes("useIsDesktop"), "the page does not choose a composition by viewport");
  assert.ok(/desktop \? \(\s*(?:<>\s*)?<SimilarStoriesPanel/.test(PAGE), "the card is not gated to desktop");
  // …and the desktop rail reads stories first, then the topics that open onto more of them.
  const card = PAGE.indexOf("<SimilarStoriesPanel");
  const topics = PAGE.indexOf("<StoryTopics", card);
  assert.ok(card > -1 && topics > card, "Similar news topics must follow the card on desktop");
});

test("below `lg` the page is the one it always was", () => {
  assert.ok(existsSync(join(WEB, "components", "stories", "similar-stories.tsx")),
    "the horizontal rail is mobile's Similar Stories surface and must still exist");
  // `SimilarStoriesPanel` shares the prefix, so match the element name exactly.
  assert.ok(/<SimilarStories(?![A-Za-z])/.test(PAGE), "the rail is no longer rendered anywhere");
  assert.ok(/desktop === false && \(\s*<SimilarStories(?![A-Za-z])/.test(PAGE),
    "the rail must render below `lg` only — on desktop it would repeat the card");
  assert.ok(PAGE.includes("<RecommendationPanel"), "Picked for you must stay on the mobile page");
  // Its rail order is the one that shipped: topics above the reader's own feed.
  const mobile = PAGE.lastIndexOf("<StoryTopics");
  assert.ok(mobile > -1 && PAGE.indexOf("<RecommendationPanel") > mobile,
    "below `lg` the topics section must keep its place above Picked for you");
  // …and neither viewport pays for the other's feed.
  assert.ok(/useRecommendations\(undefined, desktop === false\)/.test(PAGE),
    "the recommendations query must be off wherever Picked for you is not rendered");
});

test("the card tells loading, failure and absence apart", () => {
  // An empty array is what all three look like from here, so the query's STATE is passed in and
  // each has its own render. Reporting "nothing is similar" while the request is in flight — or
  // after it failed — would be the card inventing a fact from a missing one.
  for (const marker of ["isLoading", "isError", "onRetry"]) {
    assert.ok(PANEL.includes(marker), `the card cannot see ${marker}`);
    assert.ok(PAGE.includes(`${marker}=`), `the story page does not hand the card ${marker}`);
  }
  assert.ok(RAIL.includes("isLoading") && RAIL.includes("isError"),
    "the rail below `lg` must keep the same three states");
  assert.ok(PANEL.includes('t("story.similar.error")'), "a failed query must say so");
  assert.ok(PANEL.includes('t("story.similar.none")'), "a genuine absence must be stated");
  assert.ok(PANEL.includes("<Skeleton"), "an in-flight query must hold the card's height");
});

test("Picked for you itself is untouched", () => {
  // It is still the home page's card; replacing it on the story page must not have edited it.
  assert.ok(REFERENCE.includes('t("home.recs.title")'));
  assert.ok(REFERENCE.includes('href="/recommendations"'));
});
