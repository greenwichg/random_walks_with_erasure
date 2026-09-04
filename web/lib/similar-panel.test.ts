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
  // The card sits inside the page's desktop-gated branch, above the mobile stack that follows it.
  const gate = PAGE.indexOf("desktop ? (");
  const card = PAGE.indexOf("<SimilarStoriesPanel");
  const stack = PAGE.indexOf("<StorySections>");
  assert.ok(gate > -1 && card > gate, "the card is not inside a desktop-gated branch");
  assert.ok(stack > card, "the mobile stack must come after the desktop rail's card");
  // …and the desktop rail reads stories first, then the topics that open onto more of them.
  const topics = PAGE.indexOf("<StoryTopics", card);
  assert.ok(topics > card && topics < stack, "Related Topics must follow the card on desktop");
});

test("below `lg`, the horizontal rail is the Similar Stories surface", () => {
  assert.ok(existsSync(join(WEB, "components", "stories", "similar-stories.tsx")),
    "the horizontal rail is mobile's Similar Stories surface and must still exist");
  // `SimilarStoriesPanel` shares the prefix, so match the element name exactly.
  assert.ok(/<SimilarStories(?![A-Za-z])/.test(PAGE), "the rail is no longer rendered anywhere");
  // It lives in the mobile stack, which is the page's other branch — never beside the card.
  const stack = PAGE.indexOf("<StorySections>");
  const rail = PAGE.search(/<SimilarStories(?![A-Za-z])/);
  assert.ok(stack > -1 && rail > stack,
    "the rail must render inside the mobile stack — on desktop it would repeat the card");
  // …and below `lg` the sections read coverage → what this is about → what else covers it.
  const at = (needle: string) => PAGE.indexOf(needle, stack);
  assert.ok(at('id="story-coverage"') < at('id="story-topics"'), "coverage comes before topics");
  assert.ok(at('id="story-topics"') < at('id="story-similar"'), "topics come before similar stories");
  // One mount per viewport — no module may render twice on the same page.
  assert.equal(PAGE.split("<StoryTopics").length - 1, 2, "one StoryTopics per composition");
  assert.equal(PAGE.split("<CoverageList").length - 1, 2, "one CoverageList per composition");
});

test("every mobile section is a collapsible panel over a headless module", () => {
  const stack = PAGE.indexOf("<StorySections>");
  assert.ok(stack > -1, "the mobile stack is gone");
  // A panel supplies the heading, the description and the collapse; the module inside it must
  // therefore drop its own heading, or the section says its name twice.
  const tail = PAGE.slice(stack);
  for (const el of [
    "<StoryIntelligencePanel",
    "<StoryBreakdown",
    "<FramingComparison",
    "<CoverageList",
    "<StoryTopics",
    "<SimilarStories",
  ]) {
    const i = tail.indexOf(el);
    assert.ok(i > -1, `${el} is not in the mobile stack`);
    assert.ok(/headless/.test(tail.slice(i, i + 260)), `${el} must render headless in a panel`);
  }
  // Every panel carries a description — the line that makes a collapsed section legible.
  const SECTION = readFileSync(join(WEB, "components", "stories", "story-section.tsx"), "utf8");
  assert.ok(/description: string;/.test(SECTION), "the description must be required, not optional");
  assert.equal((PAGE.match(/description=\{t\("story\.section\./g) ?? []).length, 6,
    "all six sections must describe themselves");
});

test("the story page carries no personalised feed on either viewport", () => {
  // "Picked for you" held the rail slot on both. The card replaced it on desktop; on mobile it was
  // removed outright, so a story page is now entirely about the story. A card that renders nothing
  // is not enough — the QUERY has to go too, or every reader still pays for a feed nobody sees.
  assert.ok(!PAGE.includes("RecommendationPanel"), "Picked for you is still on the story page");
  assert.ok(!PAGE.includes("useRecommendations"), "the recommendations query is still being made");
  // The mobile home page dropped its copy too, so nothing renders "Picked for you" any more —
  // but the component stays, because this file reads it as the design reference the card wears.
  const home = readFileSync(join(WEB, "components", "home", "home-mobile.tsx"), "utf8");
  assert.ok(!home.includes("<RecommendationPanel"), "Picked for you is still on the mobile home page");
  assert.ok(!home.includes("<InformationHealthPanel"), "Your Information Health is still there");
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
  // Nothing renders it now, but it is the design reference the Similar Stories card is measured
  // against by the first test in this file, so it must stay as it is rather than be deleted.
  assert.ok(REFERENCE.includes('t("home.recs.title")'));
  assert.ok(REFERENCE.includes('href="/recommendations"'));
});
