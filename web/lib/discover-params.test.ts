import { test } from "node:test";
import assert from "node:assert/strict";
import { discoverKey, type DiscoverFilters } from "./discover-params.ts";

test("no filters and the empty record share the Global identity", () => {
  assert.deepEqual(discoverKey(), discoverKey({}));
  assert.deepEqual([...discoverKey()], ["discover", "all", "all", "all", "all", "default"]);
});

test("regression: switching only the country changes the identity (the frozen-filter bug)", () => {
  const us = discoverKey({ country: "US", limit: 200 });
  const gb = discoverKey({ country: "GB", limit: 200 });
  assert.notDeepEqual(us, gb);
  assert.ok([...us].includes("US") && [...gb].includes("GB"));
  // Global (no country) is a distinct cache entry from any selected country.
  assert.notDeepEqual(discoverKey({ limit: 200 }), us);
});

// The drift ratchet: `Required<DiscoverFilters>` fails typecheck the moment a filter field is
// added to the type without being named here — and the assertion then proves the new field is
// part of the cache identity. This is what keeps "service sends it, key ignores it" impossible.
test("ratchet: every DiscoverFilters field is a key segment", () => {
  const every: Required<DiscoverFilters> = {
    topic: "Politics",
    publisher: "NPR",
    lean: "left",
    country: "US",
    limit: 24,
  };
  const key = [...discoverKey(every)];
  for (const value of Object.values(every)) assert.ok(key.includes(value as never));
  assert.equal(key.length, Object.keys(every).length + 1);   // "discover" prefix + one per field
});
