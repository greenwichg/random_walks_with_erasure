import { test } from "node:test";
import assert from "node:assert/strict";
import { requestParams } from "./request-params.ts";
import type { SearchParams, StoryQuery } from "../types/domain.ts";

test("drops the unfiltered spellings, keeps real values (incl. 0), stringifies numbers", () => {
  assert.deepEqual(
    requestParams({ topic: undefined, publisher: null, lean: "", sort: "all", query: "climate", offset: 0, limit: 12 }),
    { query: "climate", offset: "0", limit: "12" },
  );
  assert.deepEqual(requestParams({}), {});
});

test("caller property order never changes the identity record", () => {
  assert.deepEqual(
    requestParams({ country: "US", sort: "newest", limit: 12 } satisfies SearchParams),
    requestParams({ limit: 12, sort: "newest", country: "US" } satisfies SearchParams),
  );
});

test("regression: switching only the country changes the identity (the frozen-filter bug)", () => {
  const us = requestParams({ country: "US", sort: "newest", limit: 12 } satisfies SearchParams);
  const gb = requestParams({ country: "GB", sort: "newest", limit: 12 } satisfies SearchParams);
  assert.notDeepEqual(us, gb);
  assert.equal(us.country, "US");
  assert.equal(gb.country, "GB");
});

// The drift ratchet: `Required<…>` means adding a field to either request type fails typecheck
// here until the sample names it — and the assertion then proves the new field is part of the
// wire record, hence part of the cache key. A param can never again reach the URL but not the key.
test("every SearchParams field is part of the request identity", () => {
  const full: Required<SearchParams> = {
    query: "climate",
    publisher: "Reuters",
    lean: "left",
    topic: "Politics",
    dateFrom: "2026-01-01",
    dateTo: "2026-02-01",
    source: "rss",
    country: "US",
    sort: "newest",
    limit: 12,
    offset: 24,
  };
  assert.deepEqual(Object.keys(requestParams(full)).sort(), Object.keys(full).sort());
});

test("every StoryQuery field is part of the request identity", () => {
  const full: Required<StoryQuery> = {
    topic: "Politics",
    publisher: "Reuters",
    lean: "left",
    country: "US",
    dateFrom: "2026-01-01",
    dateTo: "2026-02-01",
    sort: "top",
    limit: 24,
    offset: 24,
  };
  assert.deepEqual(Object.keys(requestParams(full)).sort(), Object.keys(full).sort());
});
