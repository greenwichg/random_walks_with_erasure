import { test } from "node:test";
import assert from "node:assert/strict";
import { partitionByCountryMatch } from "./country-partition.ts";

/** A card, identified so order is checkable. */
const C = (id: string, countryMatch?: boolean) => ({ id, countryMatch });
const ids = (xs: { id: string }[]) => xs.map((x) => x.id).join(",");

test("no country selected → the list is untouched and no boundary is drawn", () => {
  const items = [C("a"), C("b"), C("c")];
  const { ordered, firstBackfill } = partitionByCountryMatch(items);
  assert.equal(ordered, items); // the very same array — provably no reordering
  assert.equal(firstBackfill, -1);
});

test("the per-strategy interleave is flattened: every country card rises above the boundary", () => {
  // The exact shape the engine serves: matched-first WITHIN each strategy group, never globally.
  // Bridging contributes 3 matched then 2 backfill; Discovery and For You then contribute their
  // own matched cards, which the old single-boundary logic stranded below the divider.
  const items = [
    C("b1", true), C("b2", true), C("b3", true), C("b4", false), C("b5", false),
    C("d1", true), C("d2", false),
    C("f1", true), C("f2", false),
  ];
  const { ordered, firstBackfill } = partitionByCountryMatch(items);
  assert.equal(ids(ordered), "b1,b2,b3,d1,f1,b4,b5,d2,f2");
  assert.equal(firstBackfill, 5);
  // everything above the boundary matched; everything below did not
  assert.ok(ordered.slice(0, firstBackfill).every((r) => r.countryMatch === true));
  assert.ok(ordered.slice(firstBackfill).every((r) => r.countryMatch === false));
  assert.equal(ordered.length, items.length); // a reorder, never a filter
});

test("each part keeps its blend order", () => {
  const items = [C("a", true), C("b", false), C("c", true), C("d", false), C("e", true)];
  const { ordered } = partitionByCountryMatch(items);
  assert.equal(ids(ordered), "a,c,e,b,d");
});

test("the country filled every slot → no boundary", () => {
  const { ordered, firstBackfill } = partitionByCountryMatch([C("a", true), C("b", true)]);
  assert.equal(ids(ordered), "a,b");
  assert.equal(firstBackfill, -1, "nothing to announce when nothing was backfilled");
});

test("the country matched nothing → the boundary is first, so the feed never lies", () => {
  const { ordered, firstBackfill } = partitionByCountryMatch([C("a", false), C("b", false)]);
  assert.equal(firstBackfill, 0);
  assert.equal(ids(ordered), "a,b");
});

test("an empty feed yields no boundary", () => {
  const { ordered, firstBackfill } = partitionByCountryMatch([]);
  assert.deepEqual(ordered, []);
  assert.equal(firstBackfill, -1);
});
