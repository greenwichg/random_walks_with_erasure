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
// A3/A4 authenticated (measured-reader) goldens — the SAME fixtures the backend enrichment test pins.
const authedBridge = golden("authed_bridge");
const authedFamiliar = golden("authed_familiar");
const authedFollowing = golden("authed_following");

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

test("anonymous reader sections are null (A2 preserved); the presentation key set is closed", () => {
  const p = analysisPresentation(catalogHit);
  assert.equal(p.recommendation, null); // anonymous / non-measured → no reader sections
  assert.equal(p.explanation, null);
  assert.equal(p.personal, null); // A4 — mapped, and null for anonymous input
  assert.deepEqual(Object.keys(p).sort(),
    ["explanation", "notes", "personal", "provenance", "recommendation", "scoring", "status", "story"]);
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

// --------------------------------------------------------------------------- //
// A3 — reader-relative sections. Absent (null) for the anonymous goldens; present + mapped for the
// authenticated goldens. The verdict maps the closed reason vocabulary to catalog keys (never raw
// codes); the explanation is passed through raw for the component's reused renderer.
// --------------------------------------------------------------------------- //
test("anonymous goldens carry no reader sections (A2 preserved)", () => {
  for (const r of [catalogHit, scoredUrlOnly, invalid]) {
    const p = analysisPresentation(r);
    assert.equal(p.recommendation, null);
    assert.equal(p.explanation, null);
    assert.equal(p.personal, null);
  }
});

test("authed bridge: broaden verdict + localized cross-cutting/new-publisher reasons", () => {
  const p = analysisPresentation(authedBridge);
  assert.ok(p.recommendation);
  assert.equal(p.recommendation!.wouldBroaden, true);
  assert.equal(p.recommendation!.headlineKey, "analyze.rec.broadens");
  const keys = p.recommendation!.reasons.map((r) => r.key);
  assert.ok(keys.includes("analyze.rec.reason.crossCutting"));
  assert.ok(keys.includes("analyze.rec.reason.newPublisher"));
  // explanation is passed through raw (the component reuses presentRecommendation on it)
  assert.equal(p.explanation?.type, "bridge");
});

test("authed familiar: not-broaden verdict + familiar-topic reason", () => {
  const p = analysisPresentation(authedFamiliar);
  assert.equal(p.recommendation!.wouldBroaden, false);
  assert.equal(p.recommendation!.headlineKey, "analyze.rec.familiar");
  assert.deepEqual(p.recommendation!.reasons.map((r) => r.key), ["analyze.rec.reason.familiarTopic"]);
  assert.equal(p.explanation?.type, "topic_continuity");
});

test("blind_spot reason carries the topic param; unknown codes are dropped (no raw signal)", () => {
  const p = analysisPresentation({
    ...authedBridge,
    recommendation: { wouldBroaden: true, reasons: ["blind_spot", "totally_new_code"], blindSpotTopic: "Economy" },
  });
  const reasons = p.recommendation!.reasons;
  assert.deepEqual(reasons, [{ key: "analyze.rec.reason.blindSpot", params: { topic: "Economy" } }]);
});

test("reason keys are the closed analyze.rec.reason.* set (localization coverage)", () => {
  const all = new Set<string>();
  for (const g of [authedBridge, authedFamiliar]) {
    for (const r of analysisPresentation(g).recommendation!.reasons) all.add(r.key);
  }
  for (const k of all) assert.match(k, /^analyze\.rec\.reason\.[a-zA-Z]+$/);
});

test("a malformed recommendation degrades to null (mapper stays total)", () => {
  assert.equal(analysisPresentation({ ...authedBridge, recommendation: 42 as never }).recommendation, null);
  assert.equal(
    analysisPresentation({ ...authedBridge, explanation: { nope: true } as never }).explanation,
    null,
  );
});

// --------------------------------------------------------------------------- //
// A3.3 — recommendation.nextArticle ("Read next"), driven by the authed goldens.
// --------------------------------------------------------------------------- //
function withNext(base: AnalysisResult, nextArticle: unknown): AnalysisResult {
  return {
    ...base,
    recommendation: { ...base.recommendation!, nextArticle: nextArticle as never },
  };
}

test("authed goldens carry the story pick: label, article, explanation passthrough", () => {
  for (const g of [authedBridge, authedFamiliar]) {
    const next = analysisPresentation(g).recommendation!.next;
    assert.ok(next);
    assert.equal(next!.labelKey, "analyze.next.story");
    assert.equal(next!.article.publisher, "AP");                    // the golden's deterministic pick
    assert.ok(next!.article.headline.length > 0 && next!.article.url!.length > 0);
    assert.equal(next!.explanation?.type, "new_publisher");         // raw resolver dict, passed through
  }
});

test("feed source maps to the feed label; unknown source degrades to the generic label", () => {
  const na = authedBridge.recommendation!.nextArticle!;
  assert.equal(
    analysisPresentation(withNext(authedBridge, { ...na, source: "feed" })).recommendation!.next!.labelKey,
    "analyze.next.feed",
  );
  assert.equal(
    analysisPresentation(withNext(authedBridge, { ...na, source: "brand_new_source" })).recommendation!
      .next!.labelKey,
    "analyze.next.generic",                                         // never a raw source value
  );
});

test("null / malformed nextArticle renders nothing (next is null; verdict untouched)", () => {
  const cases: unknown[] = [
    null,
    undefined,
    42,
    { source: "story" },                                             // no article
    { source: "story", article: 7 },                                 // article not an object
    { source: "story", article: { ...authedBridge.recommendation!.nextArticle!.article, headline: "" } },
    { source: "story", article: { ...authedBridge.recommendation!.nextArticle!.article, url: "" } },
  ];
  for (const c of cases) {
    const rec = analysisPresentation(withNext(authedBridge, c)).recommendation!;
    assert.equal(rec.next, null, `case ${JSON.stringify(c)} must not render`);
    assert.equal(rec.wouldBroaden, true);                            // the verdict itself survives
  }
});

test("next labels are the closed analyze.next.* set (localization coverage)", () => {
  const na = authedBridge.recommendation!.nextArticle!;
  for (const source of ["story", "feed", "???"]) {
    const key = analysisPresentation(withNext(authedBridge, { ...na, source })).recommendation!.next!
      .labelKey;
    assert.match(key, /^analyze\.next\.[a-z]+$/i);
  }
});

// --------------------------------------------------------------------------- //
// A4.2 — `personal` (reader standing), driven by the authed goldens. Values pass through
// verbatim; the only presentation choices are display ones (never/rarely bands and blindSpot are
// already rendered as verdict reasons; shares become the resolver's whole-percent rule).
// --------------------------------------------------------------------------- //
function withPersonal(base: AnalysisResult, personal: unknown): AnalysisResult {
  return { ...base, personal: personal as AnalysisResult["personal"] };
}

test("authed bridge/familiar: topic share + viewpoint map; story stays null (no member read)", () => {
  const bridge = analysisPresentation(authedBridge).personal!;
  assert.deepEqual(bridge.topicShare, { topic: "Politics", percent: 85 });
  assert.deepEqual(bridge.viewpoint, {
    shares: { left: 0.1, center: 0.05, right: 0.85 },
    addsMissing: false,
  });
  assert.equal(bridge.alreadyRead, null);
  assert.equal(bridge.familiarPublisher, null); // "never" renders as the newPublisher reason, not here
  assert.equal(bridge.story, null);

  const familiar = analysisPresentation(authedFamiliar).personal!;
  assert.deepEqual(familiar.familiarPublisher, { name: "The Guardian" }); // the one NEW standing fact
  assert.deepEqual(familiar.topicShare, { topic: "Politics", percent: 90 });
});

test("authed following: story standing + a claimed addsMissing (the A4.1 pinned scenario)", () => {
  const p = analysisPresentation(authedFollowing).personal!;
  assert.deepEqual(p.story, { readCount: 1, bucketsRead: ["center"], addsBucket: "left" });
  assert.deepEqual(p.viewpoint, {
    shares: { left: 0.0, center: 0.25, right: 0.75 },
    addsMissing: true,
  });
  assert.deepEqual(p.topicShare, { topic: "Politics", percent: 75 });
  // and the A3 sections still map alongside (one card, three sections)
  const full = analysisPresentation(authedFollowing);
  assert.equal(full.explanation?.type, "story_match");
  assert.ok(full.recommendation!.reasons.map((r) => r.key).includes("analyze.rec.reason.following"));
});

test("alreadyRead maps with and without a timestamp", () => {
  const base = authedFollowing.personal!;
  const withAt = analysisPresentation(
    withPersonal(authedFollowing, { ...base, alreadyRead: { at: "2026-07-18T14:00:00+00:00" } }),
  ).personal!;
  assert.deepEqual(withAt.alreadyRead, { at: "2026-07-18T14:00:00+00:00" });
  const noAt = analysisPresentation(
    withPersonal(authedFollowing, { ...base, alreadyRead: { at: null } }),
  ).personal!;
  assert.deepEqual(noAt.alreadyRead, { at: null });
  const junkAt = analysisPresentation(
    withPersonal(authedFollowing, { ...base, alreadyRead: { at: 42 } }),
  ).personal!;
  assert.deepEqual(junkAt.alreadyRead, { at: null }); // read known; junk timestamp degrades to null
});

test("display dedup: never/rarely bands and a sub-1% share render nothing", () => {
  const base = authedFollowing.personal!;
  const p = analysisPresentation(
    withPersonal(authedFollowing, {
      ...base,
      publisher: { name: "The Guardian", band: "rarely" },
      topic: { topic: "Politics", share: 0.004, blindSpot: null },
    }),
  ).personal!;
  assert.equal(p.familiarPublisher, null); // the verdict reason already says it
  assert.equal(p.topicShare, null); // <1% is no claim (the resolver's own display rule)
});

test("malformed personal degrades: junk blocks null out; nothing renderable maps to null", () => {
  const base = authedFollowing.personal!;
  // Every block junked (alreadyRead/publisher are already null in this golden) -> the whole
  // section maps to null, so the component renders nothing rather than an empty heading.
  const allJunk = analysisPresentation(
    withPersonal(authedFollowing, {
      ...base,
      viewpoint: { articleBucket: "up", readerShares: { left: "x" }, addsMissing: true },
      story: { readCount: 0, bucketsRead: ["center"], addsBucket: "left" },
      topic: { topic: "", share: 0.5, blindSpot: null },
    }),
  ).personal;
  assert.equal(allJunk, null);

  const partial = analysisPresentation(
    withPersonal(authedFollowing, {
      ...base, // topic stays valid (Politics, 75%)
      viewpoint: { articleBucket: "up", readerShares: { left: "x" }, addsMissing: true },
      story: { readCount: 0, bucketsRead: ["center"], addsBucket: "left" },
    }),
  ).personal!;
  assert.equal(partial.viewpoint, null); // bad shares -> no spectrum, no addsMissing claim
  assert.equal(partial.story, null); // readCount < 1 is never a story claim
  assert.deepEqual(partial.topicShare, { topic: "Politics", percent: 75 }); // valid block survives

  for (const junk of [null, undefined, 42, "x", {}, { alreadyRead: null, publisher: null,
       topic: null, viewpoint: null, story: null }]) {
    assert.equal(analysisPresentation(withPersonal(authedFollowing, junk)).personal, null,
      `personal ${JSON.stringify(junk)} must map to null`);
  }
});

test("story standing filters unknown buckets; an unknown addsBucket never renders", () => {
  const base = authedFollowing.personal!;
  const p = analysisPresentation(
    withPersonal(authedFollowing, {
      ...base,
      story: { readCount: 2, bucketsRead: ["center", "sideways"], addsBucket: "diagonal" },
    }),
  ).personal!;
  assert.deepEqual(p.story, { readCount: 2, bucketsRead: ["center"], addsBucket: null });
});
