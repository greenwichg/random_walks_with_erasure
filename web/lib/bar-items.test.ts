import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { isLabelled, labelledItems } from "./bar-items.ts";

/**
 * The defect: the report's Reading distribution drew a nameless row — a bar, "10%", and 18 reads,
 * against no subject. `ingest.classify_topic` returns "" for an article it cannot classify (by
 * design: a guessed topic is worse than an admitted unknown) and documents that the UI hides that
 * segment. History and Home do; this card did not.
 */

const item = (label: string, value = 0.1) => ({ label, value });

test("a row that cannot name itself is not drawn", () => {
  const items = [item("Politics", 0.5), item(""), item("World", 0.08)];
  assert.deepEqual(labelledItems(items).map((i) => i.label), ["Politics", "World"]);
});

test("whitespace is blank — it renders as a blank row just the same", () => {
  for (const blank of ["", " ", "   ", "\t", "\n", " \t\n "]) {
    assert.equal(isLabelled(blank), false, `${JSON.stringify(blank)} must not count as a label`);
  }
  assert.equal(labelledItems([item(" ")]).length, 0);
});

test("a non-string label is not a label", () => {
  // Payloads arrive over the wire; a null or a number here would render "null" or "0" as a name.
  for (const bad of [null, undefined, 0, 42, {}, []]) {
    assert.equal(isLabelled(bad), false, `${JSON.stringify(bad)} must not count as a label`);
  }
  assert.equal(labelledItems([{ label: null } as never, item("Politics")]).length, 1);
});

test("real labels are untouched, including ones that look odd", () => {
  const odd = ["U.S.", "r/Conservative", "Arts & Culture", "0", "—", "Ελλάδα", "日本"];
  for (const label of odd) assert.equal(isLabelled(label), true, `${label} is a real name`);
  const items = odd.map((l) => item(l));
  assert.deepEqual(labelledItems(items), items);
});

test("order and identity are preserved — this is a filter, never a re-rank", () => {
  const items = [item("A", 0.5), item(""), item("B", 0.3), item("  "), item("C", 0.2)];
  const out = labelledItems(items);
  assert.deepEqual(out.map((i) => i.label), ["A", "B", "C"]);
  assert.equal(out[0], items[0], "the surviving objects are the same objects");
});

test("everything blank yields nothing, so the card shows its empty state", () => {
  assert.deepEqual(labelledItems([item(""), item(" ")]), []);
  assert.deepEqual(labelledItems([]), []);
});

/* ── the guards: the rule must stay where every list passes through ─────────────────────────── */

const HERE = dirname(fileURLToPath(import.meta.url));
const read = (...parts: string[]) => readFileSync(join(HERE, "..", ...parts), "utf-8");

test("BarList itself drops unlabelled rows, so no call site can reintroduce one", () => {
  const src = read("components", "shared", "bar-list.tsx");
  assert.match(src, /labelledItems\(/, "BarList must filter its own input");
  // It keys rows by label; two blanks would be one React key. The filter is what prevents that.
  assert.match(src, /key=\{item\.label\}/, "if the key changes, re-check the duplicate-key note");
});

test("the report filters BEFORE it slices, so a blank never costs a real category its slot", () => {
  // topics arrive ranked and are cut to eight. Filtering after the cut would show seven rows while
  // a ninth real category waited outside — the bug fixed twice over, badly.
  const src = read("app", "(app)", "report", "page.tsx");
  const filterAt = src.indexOf("isLabelled(tp.topic)");
  const sliceAt = src.indexOf(".slice(0, 8)");
  assert.ok(filterAt > 0 && sliceAt > 0, "the topic list must still filter and slice");
  assert.ok(filterAt < sliceAt, "filter must come before slice");
});

test("BlindSpots drops an entry that cannot name its topic", () => {
  // The engine no longer emits these, but the widget keys by `b.topic` — two unnamed entries would
  // be one React key — and its note is engine-composed prose that leads with the topic, so a blank
  // one renders " is 31% of what's available…". Second line of defence, and the reason for it.
  const src = read("components", "report", "report-widgets.tsx");
  assert.match(src, /isLabelled\(b\.topic\)/, "BlindSpots must filter unnamed entries");
  const filterAt = src.indexOf("isLabelled(b.topic)");
  const mapAt = src.indexOf(".map((b, i) =>");
  assert.ok(filterAt > 0 && filterAt < mapAt, "the filter must run before the rows are built");
});
