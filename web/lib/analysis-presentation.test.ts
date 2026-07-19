import { test } from "node:test";
import assert from "node:assert/strict";
import { analysisPresentation } from "./analysis-presentation.ts";
import type { AnalysisResult } from "../types/domain.ts";

/** A minimal analyzed result; override per case. */
function result(over: Partial<AnalysisResult> = {}): AnalysisResult {
  return {
    analysisVersion: 1,
    input: { url: "https://ap.example.com/x", canonicalUrl: "https://ap.example.com/x" },
    status: "analyzed",
    source: "scored_url_only",
    article: null,
    scoring: {
      outlet: "Associated Press",
      lean: 0.0,
      leanBucket: "center",
      topic: "Politics",
      political: true,
      emotion: { fear: 0.1, outrage: 0.1, analysis: 0.2, positive: 0.1, neutral: 0.5 },
      register: 0.8,
      confidence: 0.6,
    },
    story: null,
    recommendation: null,
    explanation: null,
    personal: null,
    notes: [],
    ...over,
  };
}

test("provenance: catalog vs scored-url-only vs none", () => {
  assert.equal(analysisPresentation(result({ source: "catalog" })).provenance?.labelKey, "analyze.provenance.catalog");
  assert.equal(analysisPresentation(result({ source: "catalog" })).provenance?.variant, "positive");
  assert.equal(
    analysisPresentation(result({ source: "scored_url_only" })).provenance?.labelKey,
    "analyze.provenance.scoredUrlOnly",
  );
  assert.equal(analysisPresentation(result({ source: "scored_url_only" })).provenance?.variant, "caution");
  assert.equal(analysisPresentation(result({ status: "invalid_url", source: null })).provenance, null);
});

test("lean: a known bucket carries the value; an unknown outlet is explicit, never guessed", () => {
  const known = analysisPresentation(result({ scoring: { ...result().scoring!, lean: -1, leanBucket: "left" } }));
  assert.deepEqual(known.scoring?.lean, { known: true, lean: -1, bucket: "left" });

  const unknown = analysisPresentation(result({ scoring: { ...result().scoring!, lean: null, leanBucket: null } }));
  assert.deepEqual(unknown.scoring?.lean, { known: false });
});

test("register and confidence are never surfaced (deferred)", () => {
  const p = analysisPresentation(result());
  assert.ok(p.scoring && !("register" in p.scoring), "no register in scoring presentation");
  assert.ok(p.scoring && !("confidence" in p.scoring), "no confidence in scoring presentation");
  assert.deepEqual(Object.keys(p.scoring!).sort(), ["emotionKey", "lean", "outlet", "political", "topic"]);
});

test("dominant emotion is the max share; absent emotion → no chip", () => {
  assert.equal(analysisPresentation(result()).scoring?.emotionKey, "neutral");
  const noEmo = analysisPresentation(result({ scoring: { ...result().scoring!, emotion: null } }));
  assert.equal(noEmo.scoring?.emotionKey, null);
});

test("empty topic collapses to null (row hidden)", () => {
  const p = analysisPresentation(result({ scoring: { ...result().scoring!, topic: "" } }));
  assert.equal(p.scoring?.topic, null);
});

test("story: membership vs advisory vs none vs absent", () => {
  const member = analysisPresentation(
    result({
      source: "catalog",
      story: {
        matched: true,
        storyId: "s1",
        articleCount: 3,
        publisherCount: 3,
        distribution: { left: 0.5, center: 0.5, right: 0 },
        missingViewpoints: ["right"],
      },
    }),
  );
  assert.equal(member.story?.kind, "member");
  assert.deepEqual(member.story?.kind === "member" && member.story.missingViewpoints, ["right"]);

  const similar = analysisPresentation(
    result({ story: { matched: false, similarStory: { storyId: "s2", similarity: 0.4 } } }),
  );
  assert.equal(similar.story?.kind, "similar");

  const none = analysisPresentation(result({ story: { matched: false, similarStory: null } }));
  assert.equal(none.story?.kind, "none");

  assert.equal(analysisPresentation(result({ story: null })).story, null);
});

test("deferred sections (recommendation/explanation/personal) are never modelled", () => {
  const keys = Object.keys(analysisPresentation(result()));
  for (const k of ["recommendation", "explanation", "personal"]) {
    assert.ok(!keys.includes(k), `presentation has no ${k}`);
  }
  assert.deepEqual(keys.sort(), ["notes", "provenance", "scoring", "status", "story"]);
});

test("notes: known notes localize; an unrecognized note is preserved as technical, not dropped", () => {
  const p = analysisPresentation(
    result({
      notes: [
        "no page metadata supplied — scoring degrades to URL-level signals (outlet, path section)",
        "outlet not in the registry — political lean unknown (excluded from lean-based claims, as everywhere)",
        "some brand-new backend note we don't recognize yet",
      ],
    }),
  );
  assert.deepEqual(p.notes[0], { kind: "known", key: "analyze.note.noMetadata" });
  assert.deepEqual(p.notes[1], { kind: "known", key: "analyze.note.unknownOutlet" });
  assert.deepEqual(p.notes[2], { kind: "technical", text: "some brand-new backend note we don't recognize yet" });
});

test("invalid_url: no provenance/scoring/story, notes still surface", () => {
  const p = analysisPresentation(
    result({ status: "invalid_url", source: null, scoring: null, story: null, notes: ["not a fetchable URL (no plausible host)"] }),
  );
  assert.equal(p.status, "invalid_url");
  assert.equal(p.provenance, null);
  assert.equal(p.scoring, null);
  assert.equal(p.story, null);
  assert.deepEqual(p.notes[0], { kind: "known", key: "analyze.note.invalidUrl" });
});

test("pure + deterministic: identical input → deep-equal output", () => {
  const r = result({ source: "catalog", notes: ["no page metadata here"] });
  assert.deepEqual(analysisPresentation(r), analysisPresentation(r));
});
