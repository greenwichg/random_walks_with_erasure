// The guard that keeps `/api/me/*` from growing a second authentication model.
//
// Phase 1 replaced twelve hand-written auth checks with one shared helper. The value of that is not
// the deduplication — it is that a bearer token which was revoked is now refused everywhere by the
// same code. That property survives exactly as long as the next route to be added uses the helper,
// and nothing about writing a route handler prompts anyone to. The previous shape was two lines
// (`engineAuthHeaders()` then a truthiness check on the header) and it was correct-looking whether
// or not you had thought about tokens at all.
//
// So this file scans the tree rather than testing behaviour. It fails on a new route the moment it
// exists, in `npm test`, with the rule in the message — not in review, and not in production when a
// mobile client discovers that one endpoint still answers only to cookies.
//
// It deliberately has NO allowlist. An exemption mechanism is a place for the next exception to be
// filed, and a rule that can be opted out of is guidance, not a guard. If a route under `/api/me/`
// genuinely must not use the shared check, the honest fix is to move it out of `/api/me/`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

const WEB = join(import.meta.dirname, "..");
const ME = join(WEB, "app", "api", "me");

/** Route handlers, by the HTTP method each exports — the things that need an auth decision. */
const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;

/** The shared entry points. Either one satisfies the rule; which is right is the route's call. */
const HELPERS = ["requireUser", "optionalUser"] as const;

/** The session-only helper the shared check replaced. Direct use is what this guard forbids. */
const BYPASSES = ["engineAuthHeaders", "engineHeadersForUserId", "resolveApiToken", "bearerToken"];

function routeFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...routeFiles(full));
    else if (entry === "route.ts" || entry === "route.tsx") out.push(full);
  }
  return out;
}

const ROUTES = routeFiles(ME);
const label = (file: string) => "/" + relative(WEB, file).split(sep).join("/");
const code = (file: string) => stripComments(readFileSync(file, "utf8"));

/**
 * Source with comments removed, because the guard scans for identifiers and a comment that NAMES
 * one is not a use of it.
 *
 * This was not hypothetical. The first version of the session-only assertion below counted
 * occurrences of `SESSION_ONLY`, and the doc comment in `tokens/route.ts` explaining why the option
 * is there counted as one — so deleting a real `SESSION_ONLY` argument still left the count high
 * enough to pass. A mutation test caught it; the fix is to stop reading prose as code.
 *
 * `//` is only treated as a line comment when it is not preceded by `:`, so a `https://` inside a
 * string literal does not swallow the rest of its line.
 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/** Walk from the delimiter at `from` to its match, or `-1`. */
function matchDelimiter(source: string, from: number, open: string, close: string): number {
  let depth = 0;
  for (let i = from; i < source.length; i++) {
    if (source[i] === open) depth++;
    else if (source[i] === close && --depth === 0) return i;
  }
  return -1;
}

/**
 * The body of one exported handler.
 *
 * A per-file check would pass a route whose GET is authenticated and whose DELETE is not — which is
 * precisely the mistake worth catching, since the unauthenticated one is usually the method added
 * later.
 *
 * The parameter list is skipped by paren matching BEFORE the body's brace is looked for, because a
 * dynamic route's signature destructures — `POST(request, { params }: { params: { id: string } })`
 * — and taking the first `{` after the name lands inside the arguments. The first draft did exactly
 * that and the guard reported the two `[id]` routes as unauthenticated; they were not. Crude brace
 * matching is fine on source we control and format, but only once it starts at the right brace.
 */
function handlerBody(source: string, method: string): string | null {
  const signature = new RegExp(`export\\s+(?:async\\s+)?function\\s+${method}\\s*\\(`);
  const start = signature.exec(source);
  if (!start) return null;
  const params = matchDelimiter(source, start.index + start[0].length - 1, "(", ")");
  if (params === -1) return null;
  const open = source.indexOf("{", params);
  if (open === -1) return null;
  const close = matchDelimiter(source, open, "{", "}");
  return source.slice(open, close === -1 ? undefined : close + 1);
}

test("there are routes under /api/me to guard (the scan itself is not silently empty)", () => {
  // Without this, a moved directory or a renamed path turns every assertion below into a loop over
  // nothing, and the guard reports success for checking zero files.
  assert.ok(ROUTES.length >= 12, `expected the /api/me routes, found ${ROUTES.length}`);
});

test("every /api/me route imports the shared authentication helper", () => {
  for (const file of ROUTES) {
    assert.match(
      code(file),
      /from\s+"@\/lib\/require-user"/,
      `${label(file)} does not import the shared check.\n` +
        `  Every route under app/api/me/ must authenticate through lib/require-user.ts:\n` +
        `    requireUser(request, "…")  — refuse a caller with no identity (a 401 today)\n` +
        `    optionalUser(request)      — the route also serves anonymous callers\n` +
        `  Both accept the session cookie AND a bearer token, and both refuse a token that does\n` +
        `  not resolve. A hand-written check will not.`,
    );
  }
});

test("no /api/me route reaches past the helper to the session-only primitives", () => {
  // The failure this prevents is not a missing check — it is a check that looks complete and is
  // session-only, which is what every one of these routes had before Phase 1. A route built on
  // `engineAuthHeaders()` authenticates the web perfectly and is invisible to every mobile client.
  for (const file of ROUTES) {
    const source = code(file);
    for (const bypass of BYPASSES) {
      assert.ok(
        !new RegExp(`\\b${bypass}\\s*\\(`).test(source),
        `${label(file)} calls ${bypass}() directly.\n` +
          `  That is the session-only path (or a raw token primitive). Use requireUser /\n` +
          `  optionalUser from lib/require-user.ts, which handle both credentials in one place.`,
      );
    }
  }
});

test("every exported handler runs the check — not just the first method in the file", () => {
  let checked = 0;
  for (const file of ROUTES) {
    const source = code(file);
    for (const method of METHODS) {
      const body = handlerBody(source, method);
      if (body === null) continue;
      checked++;
      assert.ok(
        HELPERS.some((helper) => new RegExp(`\\b${helper}\\s*\\(`).test(body)),
        `${label(file)} exports ${method} but its body never calls requireUser() or ` +
          `optionalUser().\n  An unauthenticated method in an otherwise-authenticated file is the ` +
          `shape this guard exists for.`,
      );
    }
  }
  // 16 handlers across the 12 route files at the time of writing. Pinned as a floor so a broken
  // body extractor cannot silently check nothing and pass — the same reason the file count is
  // pinned above. It is a floor, not an equality: adding a route should not fail this test, it
  // should be *caught* by the assertion inside the loop.
  assert.ok(checked >= 16, `expected to have checked every handler, saw ${checked}`);
});

test("token management stays session-only — a token may not mint or revoke tokens", () => {
  // Pinned here rather than left to review because it is the one place where accepting a bearer
  // token would be a privilege escalation: a stolen token that can mint outlives its own revocation,
  // and one that can revoke can lock the owner out with the credential they are removing.
  for (const name of ["tokens/route.ts", "tokens/[id]/route.ts"]) {
    // Each CALL is inspected, not counted: the argument list is what carries the policy, and a
    // count can be satisfied by an occurrence somewhere else in the file.
    const calls = code(join(ME, ...name.split("/"))).match(/requireUser\s*\([^)]*\)/g) ?? [];
    assert.ok(calls.length > 0, `/api/me/${name} does not call requireUser()`);
    for (const call of calls) {
      assert.ok(
        call.includes("SESSION_ONLY"),
        `/api/me/${name}: \`${call}\` does not pass SESSION_ONLY.\n` +
          `  Token management must not accept a bearer token — a token that can mint tokens\n` +
          `  outlives its own revocation, and one that can revoke can lock the owner out.`,
      );
    }
  }
});
