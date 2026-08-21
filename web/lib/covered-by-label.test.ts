import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

/**
 * The Stories / Discover / Search filter says "Covered by", and the History one says "Lean".
 *
 * They are different questions and the difference is measurable. `story_service.list_stories`
 * matches on `distribution[side] > 0.0` — *any* voting outlet on that side — so on production
 * **677 of 1522 stories (44%) are returned under more than one setting**, and a 22-publisher story
 * with 67% left-leaning coverage is returned under "Right" because ~3 outlets were right-leaning.
 * Labelled "Lean", that reads as a claim about the story's politics, which the engine never makes:
 * the field is annotated "coverage, not opinion".
 *
 * History is the opposite case and keeps "Lean" correctly — it filters ONE article by ONE outlet's
 * own bucket (`leanBucket(a.lean) !== lean`), where a lean claim is exactly what is being made.
 *
 * So the two labels must stay distinct, and neither may drift back onto the other's surface. That
 * is what this file holds, because it is invisible to every other test: a wrong label renders
 * perfectly and breaks nothing.
 */

const ROOT = join(import.meta.dirname, "..");
const MESSAGES = join(ROOT, "messages");
const LANGS = readdirSync(MESSAGES).filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5));

const catalog = (lang: string): Record<string, string> =>
  JSON.parse(readFileSync(join(MESSAGES, `${lang}.json`), "utf8"));

const source = (rel: string) => readFileSync(join(ROOT, rel), "utf8");

/** The three coverage surfaces, and the one lean surface. */
const COVERAGE_SURFACES = [
  "components/stories/story-browser.tsx",
  "app/(app)/search/page.tsx",
  "app/(app)/discover/page.tsx",
];
const LEAN_SURFACE = "app/(app)/history/page.tsx";

test("every language has both labels, and they are not the same string", () => {
  for (const lang of LANGS) {
    const c = catalog(lang);
    for (const key of ["filter.coveredBy", "filter.coveredByHint", "filter.lean"]) {
      assert.ok(c[key]?.trim(), `${lang}: ${key} is missing or empty`);
    }
    assert.notEqual(
      c["filter.coveredBy"],
      c["filter.lean"],
      `${lang}: "Covered by" and "Lean" translate to the same string, so the distinction the ` +
        `rename exists to draw is invisible to a reader of that language`,
    );
  }
});

test("the coverage surfaces use filter.coveredBy, never filter.lean", () => {
  for (const rel of COVERAGE_SURFACES) {
    const src = source(rel);
    assert.match(src, /filter\.coveredBy/, `${rel} should label its side filter "Covered by"`);
    assert.doesNotMatch(
      src,
      /t\("filter\.lean"\)/,
      `${rel} filters on ANY coverage from a side, not on a story's lean — the engine makes no ` +
        `lean claim about a story (distribution is "coverage, not opinion")`,
    );
  }
});

test("History keeps filter.lean, because there it is accurate", () => {
  const src = source(LEAN_SURFACE);
  assert.match(
    src,
    /t\("filter\.lean"\)/,
    "History filters one article by one outlet's own lean bucket; renaming it would make the " +
      "label less true, not more",
  );
  assert.doesNotMatch(src, /filter\.coveredBy/, "History has no coverage-side filter");
});

test("the hint says what the label cannot", () => {
  // The label alone still cannot carry the rule — "Covered by · Right" does not tell a reader that
  // a 67%-left story qualifies. The hint is what closes that gap, so it has to mention coverage
  // and it has to disclaim lean; a hint that only restated the label would be decoration.
  const en = catalog("en");
  const hint = en["filter.coveredByHint"].toLowerCase();
  assert.match(hint, /cover|report/, "the hint must say the filter is about who covered the story");
  assert.match(hint, /lean/, "the hint must say it is NOT the story's own lean");
});

test("the URL contract is untouched — the parameter is still `lean`", () => {
  // The rename is presentational. `?lean=left` is the contract with the engine, is what
  // `story_service.list_stories(lean=...)` reads, and is live in shared links and deep links.
  // Renaming the parameter would break every one of those for no reader-visible gain.
  for (const rel of COVERAGE_SURFACES) {
    const src = source(rel);
    assert.match(
      src,
      /params\.get\("lean"\)|lean: asFilter\(lean\)|"lean"/,
      `${rel} must keep reading and sending the lean query parameter`,
    );
  }
});
