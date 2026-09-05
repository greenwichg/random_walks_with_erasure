// The mobile boundary, asserted.
//
// `packages/core/guard.test.ts` stops the shared core importing React Native. This is the mirror:
// it stops the mobile app importing the web. Both directions matter, and for different reasons —
// a `react-native` import in core breaks the web build loudly, while a `next/server` import here
// would resolve at type-check time and fail inside Metro with a message about a Node builtin.
//
// The subtler rule is the second one below. Mobile may import `@ih/core` freely; what it must not do
// is reach into `web/`. A relative climb into `../web/lib/...` type-checks, bundles on a laptop
// where the file exists, and is the shape of the mistake that puts a second copy of a product rule
// on one platform.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

const MOBILE = join(import.meta.dirname, "..");

/** Packages that belong to the web, the server, or Node. None of them exist in a Metro bundle. */
const FORBIDDEN = [
  "next", "next-auth", "next-themes",
  "@radix-ui/", "recharts", "framer-motion", "lucide-react",
  "tailwind-merge", "class-variance-authority", "sonner", "cmdk", "vaul",
  "react-dom", "web-push", "jose",
];

/** Globals a phone does not have. `fetch`, `URL` and `AbortController` do exist and are fine. */
const FORBIDDEN_GLOBALS = ["document", "window", "localStorage", "sessionStorage", "matchMedia"];

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".expo" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

/**
 * Comments AND string literals removed.
 *
 * The doc comment on the first draft claimed both and the body did only the first, so this file
 * failed its own global check: `FORBIDDEN_GLOBALS` contains the literal `"document"`, and a scan
 * that reads string literals finds the word it is looking for in the list of words it is looking
 * for. Fourth appearance of this trap in the repo, and the first one where the guard caught itself.
 */
function code(source: string): string {
  let s = source.replace(/\/\*[\s\S]*?\*\//g, "");
  s = s.replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  s = s.replace(/"(?:[^"\\\n]|\\.)*"/g, '""');
  s = s.replace(/'(?:[^'\\\n]|\\.)*'/g, "''");
  s = s.replace(/`(?:[^`\\]|\\.)*`/g, "``");
  return s;
}

function specifiers(source: string): string[] {
  const out: string[] = [];
  for (const m of source.matchAll(/(?:^|\n)\s*(?:import|export)\s[\s\S]*?from\s+["']([^"']+)["']/g)) {
    out.push(m[1]);
  }
  return out;
}

const FILES = sources(MOBILE);
const label = (f: string) => relative(MOBILE, f).split(sep).join("/");

test("the scan sees the app", () => {
  assert.ok(FILES.length >= 8, `expected the mobile sources, found ${FILES.length}`);
});

test("nothing web-only is imported", () => {
  for (const file of FILES) {
    for (const spec of specifiers(readFileSync(file, "utf8"))) {
      const hit = FORBIDDEN.find((p) => spec === p || spec.startsWith(p) || spec.startsWith(p + "/"));
      assert.ok(
        !hit,
        `${label(file)} imports "${spec}".\n` +
          `  That is a web/server package and does not exist in a Metro bundle. If the app needs\n` +
          `  what it provides, the shared half belongs in @ih/core and the native half here.`,
      );
    }
  }
});

test("nothing reaches into web/", () => {
  for (const file of FILES) {
    for (const spec of specifiers(readFileSync(file, "utf8"))) {
      assert.ok(
        !/(^|\/)\.\.\/web\//.test(spec) && !spec.startsWith("@/../"),
        `${label(file)} imports "${spec}", which reaches into the web app.\n` +
          `  Shared code goes through @ih/core. A relative climb type-checks and then puts a second\n` +
          `  copy of a product rule on one platform.`,
      );
    }
  }
});

test("no browser global is referenced", () => {
  for (const file of FILES) {
    const src = code(readFileSync(file, "utf8"));
    for (const g of FORBIDDEN_GLOBALS) {
      assert.ok(
        !new RegExp(`(?<![.\\w$])${g}\\b(?!\\s*:)`).test(src),
        `${label(file)} references \`${g}\`, which does not exist on React Native.`,
      );
    }
  }
});

test("the shared core is imported, not reimplemented", () => {
  // The positive assertion, and the point of the whole exercise: the Recommendations screen and its
  // card must be built on @ih/core. A version of these files that stopped importing it would still
  // render — with its own copy of the ordering and the explanation rules, drifting from the web from
  // the day it was written.
  const screen = readFileSync(join(MOBILE, "app", "recommendations.tsx"), "utf8");
  const card = readFileSync(join(MOBILE, "components", "recommendations", "recommendation-card.tsx"), "utf8");
  const home = readFileSync(join(MOBILE, "components", "home", "home-model.ts"), "utf8");
  const hooks = readFileSync(join(MOBILE, "lib", "hooks.ts"), "utf8");
  for (const [name, source, expected] of [
    ["app/recommendations.tsx", screen, ["@ih/core/api/services", "@ih/core/logic/country-partition"]],
    ["components/recommendations/recommendation-card.tsx", card, ["@ih/core/logic/rec-presentation"]],
    ["components/home/home-model.ts", home, ["@ih/core/logic/home"]],
    ["lib/hooks.ts", hooks, ["@ih/core/api/services"]],
  ] as const) {
    for (const spec of expected) {
      assert.ok(source.includes(`"${spec}"`), `${name} no longer imports ${spec}`);
    }
  }
});
