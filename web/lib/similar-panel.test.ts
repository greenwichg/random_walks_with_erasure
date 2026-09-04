// The Similar Stories rail card, checked where it is cheapest to check: against the card it
// replaces and against the rules that keep it honest.
//
// It has no logic of its own to unit-test — the stories are the engine's ranked answer, handed in
// by the page. What CAN rot is what this file asserts: that the card still wears the shell "Picked
// for you" established, that it never grows a similarity opinion, and that it still tells loading,
// failure and genuine absence apart. That last one is inherited from the horizontal rail this card
// replaced, and is the reason it is asserted here — the rail was the only place it was written
// down, and deleting a component is exactly how such a rule goes missing.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const WEB = join(import.meta.dirname, "..");
const PANEL = readFileSync(join(WEB, "components", "stories", "similar-stories-panel.tsx"), "utf8");
const REFERENCE = readFileSync(join(WEB, "components", "home", "recommendation-panel.tsx"), "utf8");
const PAGE = readFileSync(join(WEB, "app", "(app)", "stories", "[id]", "page.tsx"), "utf8");

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

test("the story page shows it instead of Picked for you", () => {
  assert.ok(PAGE.includes("<SimilarStoriesPanel"), "the card is not on the story page");
  assert.ok(!PAGE.includes("RecommendationPanel"), "Picked for you is still on the story page");
  // …and does not pay for a feed it no longer renders.
  assert.ok(!PAGE.includes("useRecommendations"), "the recommendations query is still being made");
  assert.ok(/similar=\{related\}/.test(PAGE), "the card must be fed the engine's ranked answer");
});

test("the horizontal rail is gone, and nothing still reaches for it", () => {
  assert.ok(!existsSync(join(WEB, "components", "stories", "similar-stories.tsx")),
    "the rail component is still present");
  // `SimilarStoriesPanel` shares the prefix, so match the element name exactly.
  assert.ok(!/<SimilarStories(?![A-Za-z])/.test(PAGE), "the story page still renders the rail");
});

test("the card tells loading, failure and absence apart", () => {
  // An empty array is what all three look like from here, so the query's STATE is passed in and
  // each has its own render. Reporting "nothing is similar" while the request is in flight — or
  // after it failed — would be the card inventing a fact from a missing one.
  for (const marker of ["isLoading", "isError", "onRetry"]) {
    assert.ok(PANEL.includes(marker), `the card cannot see ${marker}`);
    assert.ok(PAGE.includes(`${marker}=`), `the story page does not hand the card ${marker}`);
  }
  assert.ok(PANEL.includes('t("story.similar.error")'), "a failed query must say so");
  assert.ok(PANEL.includes('t("story.similar.none")'), "a genuine absence must be stated");
  assert.ok(PANEL.includes("<Skeleton"), "an in-flight query must hold the card's height");
});

test("Picked for you itself is untouched", () => {
  // It is still the home page's card; replacing it on the story page must not have edited it.
  assert.ok(REFERENCE.includes('t("home.recs.title")'));
  assert.ok(REFERENCE.includes('href="/recommendations"'));
});
