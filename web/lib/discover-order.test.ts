import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { interleavePublishers } from "./discover-order.ts";

const P = (...names: string[]) => names.map((publisher, i) => ({ publisher, i }));
const order = (items: { publisher?: string }[]) => items.map((x) => x.publisher).join(",");

describe("interleavePublishers", () => {
  it("spreads a feed-poll burst instead of rendering it verbatim", () => {
    // The measured symptom: one outlet filing six rows in a run. The nearest other-publisher
    // item is pulled forward at each repeat — a permutation, never a removal.
    const out = interleavePublishers(P("S", "S", "S", "B", "C", "S"));
    assert.equal(order(out), "S,B,S,C,S,S");
    assert.equal(out.length, 6);
  });

  it("passes an already-diverse list through untouched", () => {
    assert.equal(order(interleavePublishers(P("A", "B", "C", "A"))), "A,B,C,A");
  });

  it("degrades to original order when only one publisher exists (filter active)", () => {
    // A publisher filter makes diversity impossible; the rule must not loop or reorder.
    const out = interleavePublishers(P("A", "A", "A"));
    assert.equal(order(out), "A,A,A");
    assert.deepEqual(out.map((x: { i?: number }) => x.i), [0, 1, 2]);
  });

  it("emits the trailing burst in order once alternatives run out", () => {
    assert.equal(order(interleavePublishers(P("A", "A", "A", "A", "B"))), "A,B,A,A,A");
  });

  it("is deterministic and keeps first-seen order among equals", () => {
    const items = P("A", "A", "B", "B", "A");
    assert.equal(order(interleavePublishers(items)), order(interleavePublishers(items)));
    // Ties break by position: the FIRST different-publisher item is pulled, not an arbitrary one.
    assert.deepEqual(interleavePublishers(items).map((x: { i?: number }) => x.i), [0, 2, 1, 3, 4]);
  });

  it("handles empties and missing publishers without judging them", () => {
    assert.deepEqual(interleavePublishers([]), []);
    const out = interleavePublishers([{ publisher: undefined }, { publisher: undefined }]);
    assert.equal(out.length, 2);
  });
});

// --------------------------------------------------------------------------- //
// River rhythm: bucketLabel / composeRiver / sliceRiver (the approved mock spec).
// --------------------------------------------------------------------------- //
import { bucketLabel, composeRiver, sliceRiver, BEAT_EVERY } from "./discover-order.ts";

// A fixed "now" mid-afternoon, so day-boundary math is unambiguous in every test.
const NOW = new Date("2026-08-16T15:00:00");
const ago = (mins: number) => new Date(+NOW - mins * 60_000).toISOString();
const art = (publisher: string, minsAgo: number, img = false, i = 0) =>
  ({ publisher, publishedAt: ago(minsAgo), img, i });
const beatable = (a: { img?: boolean }) => Boolean(a.img);

describe("bucketLabel", () => {
  it("assigns the four buckets from the stored publishedAt and the reader's clock", () => {
    assert.equal(bucketLabel(ago(10), NOW), "pastHour");
    assert.equal(bucketLabel(ago(60), NOW), "pastHour");        // inclusive edge
    assert.equal(bucketLabel(ago(3 * 60), NOW), "earlierToday"); // 12:00 same day
    assert.equal(bucketLabel(ago(20 * 60), NOW), "yesterday");   // 19:00 the day before
    assert.equal(bucketLabel(ago(30 * 60), NOW), "yesterday");   // 09:00 the day before
    assert.equal(bucketLabel(ago(40 * 60), NOW), "earlier");     // 23:00 TWO days back — calendar, not 48h
    assert.equal(bucketLabel(ago(70 * 60), NOW), "earlier");
  });

  it("never lets a missing or junk date claim freshness", () => {
    assert.equal(bucketLabel(undefined, NOW), "earlier");
    assert.equal(bucketLabel("not a date", NOW), "earlier");
  });

  it("tolerates small future skew as pastHour rather than inventing a bucket", () => {
    assert.equal(bucketLabel(ago(-2), NOW), "pastHour");
  });
});

describe("composeRiver", () => {
  it("emits landmarks in order, each row under a header that is true of it", () => {
    const items = [art("A", 5), art("B", 10), art("C", 200), art("D", 20 * 60)];
    const seq = composeRiver(items, { now: NOW, beatable });
    assert.deepEqual(
      seq.map((x) => (x.kind === "mark" ? `#${x.label}` : x.article.publisher)),
      ["#pastHour", "A", "B", "#earlierToday", "C", "#yesterday", "D"],
    );
  });

  it("skips empty buckets entirely — no header over nothing", () => {
    const seq = composeRiver([art("A", 200)], { now: NOW, beatable });
    assert.deepEqual(seq.map((x) => x.kind), ["mark", "row"]);
  });

  it("places a beat at every 9th article slot, pulling the next imaged within the look-ahead", () => {
    // 12 same-bucket articles; only #10 (0-indexed 9) carries an image. Slot 9 is the beat slot;
    // the look-ahead finds the imaged article one position later and pulls it forward.
    const items = Array.from({ length: 12 }, (_, i) => art(`P${i}`, 10 + i, i === 9, i));
    const seq = composeRiver(items, { now: NOW, beatable });
    const beats = seq.filter((x) => x.kind === "beat");
    assert.equal(beats.length, 1);
    assert.equal((beats[0] as { article: { i: number } }).article.i, 9);
    const slots = seq.filter((x) => x.kind !== "mark");
    assert.equal(slots.findIndex((x) => x.kind === "beat"), BEAT_EVERY - 1, "9th slot, 0-indexed 8");
    // a permutation: nothing dropped, nothing duplicated
    assert.equal(slots.length, 12);
    assert.equal(new Set(slots.map((x) => (x as { article: { i: number } }).article.i)).size, 12);
  });

  it("leaves the slot quiet when no imaged article sits within the look-ahead", () => {
    const items = Array.from({ length: 12 }, (_, i) => art(`P${i}`, 10 + i, false, i));
    const seq = composeRiver(items, { now: NOW, beatable });
    assert.equal(seq.filter((x) => x.kind === "beat").length, 0);
  });

  it("never pulls a beat across a landmark boundary", () => {
    // Slots 1-8 in pastHour, slot 9 begins earlierToday; the only imaged article is still in the
    // pastHour bucket. The beat slot may not reach BACK across the header, and the pastHour
    // imaged item must stay a quiet row in its own bucket.
    const items = [
      ...Array.from({ length: 8 }, (_, i) => art(`P${i}`, 5 + i, i === 0, i)),
      ...Array.from({ length: 4 }, (_, i) => art(`Q${i}`, 200 + i, false, 100 + i)),
    ];
    const seq = composeRiver(items, { now: NOW, beatable });
    assert.equal(seq.filter((x) => x.kind === "beat").length, 0);
  });

  it("still spreads publisher bursts, within each bucket", () => {
    const items = [art("S", 5, false, 0), art("S", 6, false, 1), art("B", 7, false, 2),
                   art("S", 200, false, 3), art("S", 201, false, 4)];
    const seq = composeRiver(items, { now: NOW, beatable });
    const names = seq.filter((x) => x.kind === "row").map((x) => (x as { article: { publisher: string } }).article.publisher);
    assert.deepEqual(names, ["S", "B", "S", "S", "S"], "spread inside pastHour; the 200-min bucket has only S");
  });

  it("is deterministic for a fixed (items, now)", () => {
    const items = Array.from({ length: 20 }, (_, i) => art(`P${i % 5}`, 10 + i, i % 3 === 0, i));
    const a = JSON.stringify(composeRiver(items, { now: NOW, beatable }));
    const b = JSON.stringify(composeRiver(items, { now: NOW, beatable }));
    assert.equal(a, b);
  });
});

describe("sliceRiver", () => {
  it("budgets ARTICLE slots only and never leaves a trailing header", () => {
    const items = [art("A", 5), art("B", 10), art("C", 200), art("D", 20 * 60)];
    const seq = composeRiver(items, { now: NOW, beatable });
    const cut = sliceRiver(seq, 2);
    assert.deepEqual(
      cut.map((x) => (x.kind === "mark" ? `#${x.label}` : x.article.publisher)),
      ["#pastHour", "A", "B"],
      "the earlierToday header must not dangle over zero rows",
    );
    assert.deepEqual(sliceRiver(seq, 0), []);
    assert.equal(sliceRiver(seq, 99).length, seq.length);
  });
});
