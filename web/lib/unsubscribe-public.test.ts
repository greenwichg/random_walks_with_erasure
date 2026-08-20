import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * `/unsubscribe` must stay OUTSIDE the auth matcher.
 *
 * The whole reason the page works is that a reader in a mail client — on a device they have never
 * signed in on, possibly years after the fact — can act on the link without an account. Put it
 * behind `withAuth` and every unsubscribe click lands on the onboarding funnel instead; the reader
 * cannot make the mail stop, and the button they reach for next is "report spam". That costs
 * deliverability for every other reader, and there is nothing in the UI that would show it.
 *
 * The matcher today is an explicit allowlist of protected paths, so this passes by construction.
 * The guard is for the day someone inverts it — a negative-lookahead matcher like
 * `/((?!onboarding|signin).*)` is a normal thing to write and would silently swallow this route.
 */

const ROOT = join(import.meta.dirname, "..");
const SOURCE = readFileSync(join(ROOT, "middleware.ts"), "utf8");

/** The matcher entries, read from the source rather than imported — `middleware.ts` pulls in
 *  `next-auth/middleware`, which needs a Next request context this runner does not have. */
function matcherPatterns(): string[] {
  const block = SOURCE.match(/matcher:\s*\[([\s\S]*?)\]/);
  assert.ok(block, "middleware.ts must export a config.matcher array");
  return [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

/** Next's matcher syntax, for the subset this file uses: a literal path, optionally followed by
 *  a `:param*` segment that also matches the bare parent. Anything containing regex syntax is
 *  treated as a raw regex, which is what Next does with it. */
function toRegExp(pattern: string): RegExp {
  if (/[()?!|\\]/.test(pattern)) return new RegExp(`^${pattern}$`);
  const body = pattern
    .replace(/\/:[A-Za-z_]+\*$/, "(?:/.*)?")
    .replace(/\/:[A-Za-z_]+/g, "/[^/]+");
  return new RegExp(`^${body}$`);
}

test("the matcher converter agrees with the paths we know are gated", () => {
  const gated = matcherPatterns().map(toRegExp);
  // If these stopped matching, a passing test below would prove nothing.
  for (const path of ["/", "/settings", "/settings/notifications", "/report", "/history/2026"]) {
    assert.ok(gated.some((re) => re.test(path)), `${path} should be behind the auth gate`);
  }
});

test("/unsubscribe is not behind the auth gate", () => {
  const gated = matcherPatterns().map(toRegExp);
  for (const path of ["/unsubscribe", "/api/unsubscribe"]) {
    const hit = matcherPatterns().find((_, i) => gated[i].test(path));
    assert.equal(
      hit,
      undefined,
      `${path} must stay public — an unsubscribe link that demands a login is a spam report. ` +
        `Matched by: ${hit}`,
    );
  }
});
