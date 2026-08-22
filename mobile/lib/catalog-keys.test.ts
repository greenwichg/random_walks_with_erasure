// Every catalog key the mobile app names must exist in the shared catalogs.
//
// This test exists because the first draft of `components/recommendation-card.tsx` referenced
// `rec.badge.crossCutting`, which is in no catalog. Nothing would have failed: `makeT`'s last
// fallback is the KEY ITSELF, chosen deliberately so a missing string is visible and greppable
// rather than a blank space. On a phone that means a card rendering the literal text
// "rec.badge.crossCutting" to a reader — a defect that survives typecheck, lint and every unit test,
// and is caught only by somebody looking at the screen.
//
// The web has the same exposure and covers it from the other direction: `scripts/check-i18n.mjs`
// scans for keys that are DEFINED and never used. This scans for keys that are USED and never
// defined, which is the failure that reaches a reader.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

const MOBILE = join(import.meta.dirname, "..");
const CATALOG = JSON.parse(
  readFileSync(
    join(MOBILE, "..", "packages", "core", "i18n", "messages", "en.json"),
    "utf8",
  ),
) as Record<string, string>;

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".expo" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.tsx?$/.test(entry) && !entry.endsWith(".test.ts")) out.push(full);
  }
  return out;
}

/**
 * Source with comments removed.
 *
 * The third time this trap has been hit in this repo, and the first two are documented in
 * `web/lib/api-auth-guard.test.ts` and `packages/core/guard.test.ts`. A guard that scans for
 * identifiers finds them in the prose EXPLAINING the identifiers — and here the prose is a comment
 * naming the very keys that were got wrong, which fails the guard with the mistake it was written
 * to prevent. Scanning code means scanning code.
 */
function code(source: string): string {
  let out = source.replace(/\/\*[\s\S]*?\*\//g, "");
  out = out.replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  // JSX comments — `{/* … */}` — are already covered by the block rule above.
  return out;
}

/**
 * Catalog keys named as string literals: `t("rec.strategy.story")`, or a value in a key map.
 *
 * Only dotted lowercase-initial tokens, which is the shape every catalog key has and almost nothing
 * else in this tree does. Keys built at runtime from a variable (`presentation.claimKey`) are NOT
 * caught here and cannot be — they come from `@ih/core`, whose own tests pin them.
 */
function literalKeys(source: string): string[] {
  const keys = new Set<string>();
  for (const m of source.matchAll(/["'`]([a-z][a-zA-Z0-9]*(?:\.[a-zA-Z0-9_-]+){1,4})["'`]/g)) {
    keys.add(m[1]);
  }
  return [...keys];
}

/** Dotted literals that look like keys but are not: file paths, mime types, package names. */
function looksLikeAKey(candidate: string): boolean {
  if (/\.(ts|tsx|js|json|png|svg|css)$/.test(candidate)) return false;
  if (candidate.includes("/")) return false;
  // A key is only interesting if its first segment is one the catalog actually uses. Without this,
  // every `expo.extra` and `content.type` in the tree would be reported as a missing translation.
  const prefix = candidate.split(".")[0];
  return Object.keys(CATALOG).some((k) => k.startsWith(prefix + "."));
}

const FILES = sources(MOBILE);

test("the scan sees the app (it is not silently checking nothing)", () => {
  assert.ok(FILES.length >= 5, `expected the mobile sources, found ${FILES.length}`);
  assert.ok(Object.keys(CATALOG).length > 900, "the English catalog looks empty");
});

test("every catalog key the app names exists", () => {
  const missing: string[] = [];
  for (const file of FILES) {
    for (const key of literalKeys(code(readFileSync(file, "utf8")))) {
      if (!looksLikeAKey(key)) continue;
      if (!(key in CATALOG)) missing.push(`${relative(MOBILE, file).split(sep).join("/")}: ${key}`);
    }
  }
  assert.deepEqual(
    missing,
    [],
    `these keys are referenced but not in the catalog — they would render as the key itself:\n  ` +
      missing.join("\n  "),
  );
});

test("the strategy labels the card uses are the ones the web uses", () => {
  // Pinned by value, not just by presence. Both platforms naming a recommendation strategy the same
  // way is the point of a shared catalog; the failure worth catching is one platform quietly
  // getting a different string because it invented a parallel key.
  assert.equal(CATALOG["rec.strategy.rwe-b"], "Other side");
  assert.equal(CATALOG["rec.strategy.rwe-d"], "Discovery");
  assert.equal(CATALOG["rec.strategy.adaptive"], "For you");
  assert.equal(CATALOG["rec.strategy.story"], "Same story");
});
