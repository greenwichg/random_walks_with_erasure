import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { analysisPresentation } from "./analysis-presentation.ts";
import type { AnalysisResult } from "../types/domain.ts";

/**
 * The canonical cases are the SHARED golden fixtures produced by the real analyzer
 * (tests/fixtures/analysis/*.json), not handwritten objects — so a contract change breaks a test on
 * at least one side (F2). The backend test (tests/test_analysis_fixtures.py) proves the analyzer
 * still emits these exact files. Edge cases below derive from a golden and override only the field
 * under test, so their base shape stays contract-anchored too.
 */
const SUPPORTED_ANALYSIS_VERSION = 1;

function golden(name: string): AnalysisResult {
  const url = new URL(`../../tests/fixtures/analysis/${name}.json`, import.meta.url);
  return JSON.parse(readFileSync(url, "utf8")) as AnalysisResult;
}
const catalogHit = golden("catalog_hit");
const scoredUrlOnly = golden("scored_url_only");
const invalid = golden("invalid_url");

/** Override the (untyped-at-runtime) story with an arbitrary value — for the F1 malformed cases. */
function withStory(base: AnalysisResult, story: unknown): AnalysisResult {
  return { ...base, story: story as AnalysisResult["story"] };
}

// --------------------------------------------------------------------------- //
// Contract anchor.
// --------------------------------------------------------------------------- //
test("golden fixtures are the supported analysis version", () => {
  for (const r of [catalogHit, scoredUrlOnly, invalid]) {
    assert.equal(r.analysisVersion, SUPPORTED_ANALYSIS_VERSION);
  }
});

// --------------------------------------------------------------------------- //
// Canonical cases — driven by the shared goldens.
// --------------------------------------------------------------------------- //
test("catalog hit: catalog provenance + scoring core + real story membership", () => {
  const p = analysisPresentation(catalogHit);
  assert.equal(p.status, "analyzed");
  assert.equal(p.provenance?.labelKey, "analyze.provenance.catalog");
  assert.equal(p.provenance?.variant, "positive");
  assert.ok(p.scoring);
  assert.equal(p.scoring!.lean.known, true); // AP / center in the fixture
  assert.equal(p.story?.kind, "member");
  assert.deepEqual(p.story?.kind === "member" && p.story.missingViewpoints, ["right"]);
});

test("scored-url-only: caution provenance; known backend note localizes", () => {
  const p = analysisPresentation(scoredUrlOnly);
  assert.equal(p.provenance?.labelKey, "analyze.provenance.scoredUrlOnly");
  assert.equal(p.provenance?.variant, "caution");
  assert.ok(p.scoring);
  // the fixture carries the "no page metadata" note -> a localized key, never dropped
  assert.ok(p.notes.some((n) => n.kind === "known" && n.key === "analyze.note.noMetadata"));
});

test("invalid url: no provenance/scoring/story; the note still localizes", () => {
  const p = analysisPresentation(invalid);
  assert.equal(p.status, "invalid_url");
  assert.equal(p.provenance, null);
  assert.equal(p.scoring, null);
  assert.equal(p.story, null);
  assert.deepEqual(p.notes[0], { kind: "known", key: "analyze.note.invalidUrl" });
});

// --------------------------------------------------------------------------- //
// Scoring — deferral + honest unknowns (derived from a golden).
// --------------------------------------------------------------------------- //
test("register and confidence are never surfaced (deferred)", () => {
  const p = analysisPresentation(catalogHit);
  assert.deepEqual(Object.keys(p.scoring!).sort(), ["emotionKey", "lean", "outlet", "political", "topic"]);
});

test("unknown outlet: lean is explicit-unknown, never guessed", () => {
  const unknown = analysisPresentation({
    ...scoredUrlOnly,
    scoring: { ...scoredUrlOnly.scoring!, lean: null, leanBucket: null },
  });
  assert.deepEqual(unknown.scoring?.lean, { known: false });
});

test("dominant emotion is the max share; absent emotion → no chip", () => {
  assert.equal(analysisPresentation(catalogHit).scoring?.emotionKey, "neutral"); // 0.4 is the max
  const noEmo = analysisPresentation({ ...catalogHit, scoring: { ...catalogHit.scoring!, emotion: null } });
  assert.equal(noEmo.scoring?.emotionKey, null);
});

test("empty topic collapses to null (row hidden)", () => {
  const p = analysisPresentation({ ...catalogHit, scoring: { ...catalogHit.scoring!, topic: "" } });
  assert.equal(p.scoring?.topic, null);
});

test("deferred sections (recommendation/explanation/personal) are never modelled", () => {
  const keys = Object.keys(analysisPresentation(catalogHit)).sort();
  assert.deepEqual(keys, ["notes", "provenance", "scoring", "status", "story"]);
});

// --------------------------------------------------------------------------- //
// Notes — the "Technical note" fallback for anything unrecognized.
// --------------------------------------------------------------------------- //
test("an unrecognized note is preserved as technical, not dropped", () => {
  const p = analysisPresentation({
    ...scoredUrlOnly,
    notes: [...scoredUrlOnly.notes, "some brand-new backend note we don't recognize yet"],
  });
  assert.ok(p.notes.some((n) => n.kind === "known" && n.key === "analyze.note.noMetadata"));
  assert.deepEqual(p.notes.at(-1), {
    kind: "technical",
    text: "some brand-new backend note we don't recognize yet",
  });
});

// --------------------------------------------------------------------------- //
// F1 — story presentation hardening. A malformed / future `matched` variant must NEVER reach
// SpectrumBar with a bad distribution; it degrades to advisory/none instead of crashing.
// --------------------------------------------------------------------------- //
test("F1: a matched story missing its distribution degrades to none, not a crash", () => {
  const p = analysisPresentation(
    withStory(catalogHit, { matched: true, storyId: "s", missingViewpoints: ["right"] }),
  );
  assert.equal(p.story?.kind, "none");
});

test("F1: a matched story with a non-numeric distribution degrades to none", () => {
  const p = analysisPresentation(
    withStory(catalogHit, { matched: true, distribution: { left: "x", center: 1, right: 0 } }),
  );
  assert.equal(p.story?.kind, "none");
});

test("F1: a truthy-but-not-true `matched` is not treated as membership", () => {
  const p = analysisPresentation(
    withStory(catalogHit, { matched: "yes", distribution: { left: 1, center: 0, right: 0 } }),
  );
  assert.equal(p.story?.kind, "none");
});

test("F1: a malformed matched story that still carries similarStory degrades to the advisory", () => {
  const p = analysisPresentation(
    withStory(catalogHit, { matched: true, similarStory: { storyId: "s2", similarity: 0.4 } }),
  );
  assert.equal(p.story?.kind, "similar");
});

test("story: advisory, none, and absent variants (valid contract v1)", () => {
  assert.equal(
    analysisPresentation(withStory(scoredUrlOnly, { matched: false, similarStory: { storyId: "s", similarity: 0.4 } }))
      .story?.kind,
    "similar",
  );
  assert.equal(
    analysisPresentation(withStory(scoredUrlOnly, { matched: false, similarStory: null })).story?.kind,
    "none",
  );
  assert.equal(analysisPresentation(withStory(scoredUrlOnly, null)).story, null);
});

// --------------------------------------------------------------------------- //
// Purity.
// --------------------------------------------------------------------------- //
test("pure + deterministic: identical input → deep-equal output", () => {
  assert.deepEqual(analysisPresentation(catalogHit), analysisPresentation(catalogHit));
});
