import { test } from "node:test";
import assert from "node:assert/strict";
import { framingComparison } from "./framing.ts";
import type { StoryCoverage } from "../types/domain.ts";

function row(over: Partial<StoryCoverage>): StoryCoverage {
  return {
    publisher: "P",
    headline: "H",
    publishedAt: "2026-08-08T10:00:00Z",
    ...over,
  } as StoryCoverage;
}

test("two sides present: ordered left→right with per-side counts", () => {
  const out = framingComparison([
    row({ publisher: "R1", leanBucket: "right", headline: "right take" }),
    row({ publisher: "L1", leanBucket: "left", headline: "left take" }),
    row({ publisher: "L2", leanBucket: "left", headline: "left again" }),
  ]);
  assert.ok(out);
  assert.deepEqual(out.map((s) => s.side), ["left", "right"]);
  assert.deepEqual(out.map((s) => s.count), [2, 1]);
});

test("one-sided coverage is not a comparison: null", () => {
  assert.equal(framingComparison([row({ leanBucket: "left" }), row({ leanBucket: "left" })]), null);
  assert.equal(framingComparison([]), null);
});

test("unknown-lean rows are excluded, never bucketed (L2.2)", () => {
  // Two rated LEFT rows + one unrated: unrated must not manufacture a second side.
  const out = framingComparison([
    row({ leanBucket: "left" }),
    row({ leanBucket: "left" }),
    row({ leanBucket: null, publisher: "Unrated Gazette" }),
  ]);
  assert.equal(out, null);
});

test("representative prefers the most recent REPORTING row", () => {
  const out = framingComparison([
    row({ leanBucket: "left", register: "opinion", publishedAt: "2026-08-08T12:00:00Z", headline: "newest but opinion" }),
    row({ leanBucket: "left", register: "reporting", publishedAt: "2026-08-08T09:00:00Z", headline: "older reporting" }),
    row({ leanBucket: "right", register: "reporting", headline: "right report" }),
  ]);
  assert.ok(out);
  assert.equal(out[0].row.headline, "older reporting");
});

test("no reporting on a side: falls back to the most recent row of any register", () => {
  const out = framingComparison([
    row({ leanBucket: "left", register: "opinion", publishedAt: "2026-08-08T08:00:00Z", headline: "early op" }),
    row({ leanBucket: "left", register: "opinion", publishedAt: "2026-08-08T11:00:00Z", headline: "late op" }),
    row({ leanBucket: "center", register: "reporting", headline: "wire copy" }),
  ]);
  assert.ok(out);
  assert.equal(out[0].row.headline, "late op");
});

test("deterministic: publishedAt ties break on publisher, then headline", () => {
  const rows = [
    row({ leanBucket: "left", publisher: "B Paper", headline: "b headline" }),
    row({ leanBucket: "left", publisher: "A Paper", headline: "a headline" }),
    row({ leanBucket: "right", headline: "other side" }),
  ];
  const a = framingComparison(rows);
  const b = framingComparison([...rows].reverse());
  assert.ok(a && b);
  assert.equal(a[0].row.publisher, "A Paper");
  assert.equal(b[0].row.publisher, "A Paper");
});

test("three sides render in spectrum order regardless of input order", () => {
  const out = framingComparison([
    row({ leanBucket: "right" }),
    row({ leanBucket: "center" }),
    row({ leanBucket: "left" }),
  ]);
  assert.ok(out);
  assert.deepEqual(out.map((s) => s.side), ["left", "center", "right"]);
});
