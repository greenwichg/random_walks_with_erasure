// The Similar Stories rail card, checked where it is cheapest to check: against the card it
// replaces and against the rule that keeps it honest.
//
// It has no logic of its own to unit-test — the stories are the engine's ranked answer, handed in
// by the page. What CAN rot is the two things this file asserts: that the card still wears the
// shell "Picked for you" established, and that it never grows a similarity opinion. Both are
// properties of the source, so both are read from it.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const WEB = join(import.meta.dirname, "..");
const PANEL = readFileSync(join(WEB, "components", "stories", "similar-stories-panel.tsx"), "utf8");
const REFERENCE = readFileSync(join(WEB, "components", "home", "recommendation-panel.tsx"), "utf8");
const PAGE = readFileSync(join(WEB, "app", "(app)", "stories", "[id]", "page.tsx"), "utf8");

/** The shell the reference established: container, header treatment, row rhythm. */
const SHELL = [
  'className="rounded-lg border bg-card p-4"',   // the card itself
  "<SectionHeader",                              // the header, not a hand-rolled one
  'actionLabel={t("home.viewAll")}',             // the "View all" treatment
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
  // One query feeds both surfaces: the card and the full rail below.
  assert.ok(/similar=\{related\}/.test(PAGE), "the card must be fed the same array as the rail");
  // Multi-line JSX, so match the prop rather than a formatting of the element.
  assert.ok(/<SimilarStories\b[\s\S]{0,200}?stories=\{related\}/.test(PAGE),
    "the rail must read the same array as the card");
});

test("Picked for you itself is untouched", () => {
  // It is still the home page's card; replacing it on the story page must not have edited it.
  assert.ok(REFERENCE.includes('t("home.recs.title")'));
  assert.ok(REFERENCE.includes('href="/recommendations"'));
});
