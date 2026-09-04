// The Similar Stories proxy must forward every query parameter the engine's endpoint declares.
//
// This file exists because of a specific, expensive failure. `app/api/stories/[id]/similar/route.ts`
// forwarded `limit` and nothing else, so `minScore` and `debug` were dropped in the web tier and
// never reached the engine. Nothing errored: the engine applied its own defaults and returned a
// well-formed answer, so seven consecutive sweeps of `?minScore=…` against production came back
// identical. Identical rows were read as a finding about the CATALOG — that an absolute similarity
// floor cannot transfer between corpus sizes — and the conclusion was built on an artifact of this
// one line. `?debug=1` returning `null` was the same bug wearing a different hat.
//
// A behavioural test would not have caught it either, because the layer under test was correct;
// what was missing was the CORRESPONDENCE between two files in two languages that no type system
// spans. So this compares them directly: the parameters `story_similar` accepts in
// examples/api_fastapi.py, against the allowlist here. Add a knob to the engine and this fails
// until the proxy carries it — which is the only moment anyone is thinking about it.
//
// The allowlist stays an allowlist, and this test does not weaken it into a pass-through: it
// asserts the two sets are EQUAL, so the route may not forward a parameter the engine does not
// declare either.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const WEB = join(import.meta.dirname, "..");
const ROUTE = join(WEB, "app", "api", "stories", "[id]", "similar", "route.ts");
const ENGINE = join(WEB, "..", "examples", "api_fastapi.py");

/** The names in the route's `FORWARDED` allowlist. */
function forwardedByTheProxy(): string[] {
  const src = readFileSync(ROUTE, "utf8");
  const decl = /const FORWARDED = \[([^\]]*)\]/.exec(src);
  assert.ok(decl, "route.ts no longer declares a FORWARDED allowlist — this test cannot check it");
  return [...decl[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

/**
 * The query parameters `story_similar` declares, read from its signature.
 *
 * Path parameters are excluded by construction: every query parameter is declared with a
 * `Query(...)` default, and `story_id` — the only path parameter — is not.
 */
function acceptedByTheEngine(): string[] {
  const src = readFileSync(ENGINE, "utf8");
  const start = src.indexOf("def story_similar(");
  assert.ok(start > 0, "examples/api_fastapi.py no longer defines story_similar");
  const end = src.indexOf("\n) -> dict:", start);
  assert.ok(end > start, "could not find the end of the story_similar signature");
  const signature = src.slice(start, end);
  // A parameter line, at the signature's own indentation, whose default is a Query(...). The
  // annotation may not span a newline: `[^=]` matches one, and a class that allowed it walked from
  // `story_id: str,` across the next line to the FIRST `= Query(` in the signature — reporting the
  // path parameter and swallowing `limit`.
  return [...signature.matchAll(/^ {4}(\w+)\s*:[^=\n]+=\s*Query\(/gm)].map((m) => m[1]);
}

test("the proxy forwards exactly the parameters the engine accepts", () => {
  const proxy = forwardedByTheProxy();
  const engine = acceptedByTheEngine();
  assert.ok(engine.length >= 2, `parsed too few engine parameters (${engine.join(", ")})`);
  assert.deepEqual(
    [...proxy].sort(),
    [...engine].sort(),
    "web/app/api/stories/[id]/similar/route.ts and examples/api_fastapi.py disagree about the " +
      "query parameters. A parameter the engine accepts but the proxy drops is invisible: the " +
      "engine answers with its default and the caller sees a plausible result.",
  );
});

test("the allowlist is applied, not the whole query string", () => {
  const src = readFileSync(ROUTE, "utf8");
  // The failure this guards is the opposite repair: someone forwarding `url.search` wholesale
  // after being bitten by a dropped parameter, which turns the route into a way to aim arbitrary
  // parameters at the engine.
  assert.ok(
    /for \(const key of FORWARDED\)/.test(src),
    "the route must iterate FORWARDED rather than pass the query string through",
  );
  assert.ok(
    !/searchParams\.toString\(\)|url\.search\b/.test(src),
    "the route appears to forward the incoming query string wholesale",
  );
});
