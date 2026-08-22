// The guard that keeps the shared core shared.
//
// `tsconfig.json` does the heavy lifting — no "DOM" in `lib`, so `document` and `window` are not
// declared and the compiler refuses them by name. This file covers what the compiler cannot see:
//
//   - `import { View } from "react-native"` type-checks perfectly well. It is still fatal here.
//   - so does `import { useState } from "react"`, and `import { NextResponse } from "next/server"`.
//   - `navigator` IS declared in a plain ES lib, and `navigator.sendBeacon` does not exist on React
//     Native. The compiler has no opinion; this file does.
//
// Scanning rather than trusting review, for the reason every guard in this repo exists: the rule is
// invisible while you are writing the module that breaks it. A shared module that imports one icon
// is not obviously wrong on the screen where it is written — it becomes wrong three months later,
// in the Expo bundler, to somebody who did not write it.
//
// NO ALLOWLIST. A rule that can be opted out of is guidance. If a module here needs the platform,
// the answer is a seam (take the value as an argument) or a different package.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

const CORE = import.meta.dirname;

/** Packages that bind a module to one platform. Prefix-matched, so scopes and subpaths count. */
const FORBIDDEN_PACKAGES = [
  "react", "react-dom", "react-native", "expo", "@expo/",
  "next", "next-auth", "next-themes",
  "@radix-ui/", "recharts", "framer-motion", "lucide-react",
  "clsx", "tailwind-merge", "class-variance-authority",
  "sonner", "cmdk", "vaul", "embla-carousel", "react-day-picker",
  "web-push", "@react-navigation/", "react-native-",
];

/**
 * Globals that do not exist (or do not mean the same thing) outside a browser.
 *
 * `fetch`, `URL`, `AbortController`, `Intl` and `crypto` are deliberately absent: they exist in
 * Hermes, and banning them would push perfectly portable code out of this package for no reason.
 * `navigator` is not banned outright — it exists in React Native — but `sendBeacon` is, because it
 * does not, and a module reaching for it has a transport in it that belongs to the platform.
 */
const FORBIDDEN_GLOBALS = [
  "document", "window", "localStorage", "sessionStorage",
  "matchMedia", "getComputedStyle", "requestAnimationFrame",
  "IntersectionObserver", "MutationObserver", "ResizeObserver",
  "HTMLElement", "HTMLDivElement", "Element", "Node",
  "sendBeacon", "ServiceWorker", "caches",
];

/**
 * Source with comments AND string literals removed.
 *
 * Both halves are load-bearing, and the first one is not hypothetical: `web/lib/api-auth-guard.ts`'s
 * first draft counted an identifier that appeared in a doc comment and passed a real violation. The
 * modules in this package have long headers that *name* `document` and `React` while explaining why
 * they avoid them — scanning the prose would fail every one of them.
 *
 * `//` is only a line comment when not preceded by `:`, so a `https://` inside a string survives
 * long enough to be blanked by the string rules below.
 */
function code(source: string): string {
  let s = source.replace(/\/\*[\s\S]*?\*\//g, "");
  s = s.replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  s = s.replace(/"(?:[^"\\\n]|\\.)*"/g, '""');
  s = s.replace(/'(?:[^'\\\n]|\\.)*'/g, "''");
  s = s.replace(/`(?:[^`\\]|\\.)*`/g, "``");
  return s;
}

/** Every import specifier in a module, in source order. Runs on raw source — imports are not comments. */
function importSpecifiers(source: string): string[] {
  const out: string[] = [];
  const rx = /(?:^|\n)\s*(?:import|export)\s[\s\S]*?from\s+["']([^"']+)["']/g;
  for (let m = rx.exec(source); m; m = rx.exec(source)) out.push(m[1]);
  const bare = /(?:^|\n)\s*import\s+["']([^"']+)["']/g;
  for (let m = bare.exec(source); m; m = bare.exec(source)) out.push(m[1]);
  return out;
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

const ALL = walk(CORE);
const SOURCE = ALL.filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"));
const label = (f: string) => relative(CORE, f).split(sep).join("/");

test("the scan sees the package (it is not silently checking nothing)", () => {
  // Without this, a renamed directory or a broken walk turns every assertion below into a loop over
  // an empty list, and the guard reports success for having checked no files at all.
  assert.ok(SOURCE.length >= 20, `expected the core modules, found ${SOURCE.length}`);
});

test("no .tsx anywhere — a component in the shared core is a category error", () => {
  const tsx = ALL.filter((f) => f.endsWith(".tsx")).map(label);
  assert.deepEqual(tsx, [], `JSX belongs in web/ or mobile/, not @ih/core: ${tsx.join(", ")}`);
});

test("no platform package is imported", () => {
  for (const file of SOURCE) {
    for (const spec of importSpecifiers(readFileSync(file, "utf8"))) {
      if (spec.startsWith(".") || spec.startsWith("@ih/")) continue;
      const hit = FORBIDDEN_PACKAGES.find((p) => spec === p || spec.startsWith(p + "/") || spec.startsWith(p));
      assert.ok(
        !hit,
        `${label(file)} imports "${spec}".\n` +
          `  @ih/core is shared by web and mobile, so it may not depend on either platform.\n` +
          `  If this module needs "${hit}", it belongs in web/ or mobile/ — or give it a seam and\n` +
          `  let the caller supply the platform half.`,
      );
    }
  }
});

test("no node: builtin outside tests — this package runs in a browser and on a phone", () => {
  for (const file of SOURCE) {
    for (const spec of importSpecifiers(readFileSync(file, "utf8"))) {
      assert.ok(
        !spec.startsWith("node:"),
        `${label(file)} imports "${spec}". Node builtins are available in tests and on the server,\n` +
          `  never in a bundle that ships to a browser or a phone.`,
      );
    }
  }
});

test("no browser global is referenced", () => {
  for (const file of SOURCE) {
    const src = code(readFileSync(file, "utf8"));
    for (const g of FORBIDDEN_GLOBALS) {
      // Not preceded by `.` or a word character (so `foo.window` and `myDocument` do not match),
      // and not an object key or a declared parameter name.
      const rx = new RegExp(`(?<![.\\w$])${g}\\b(?!\\s*:)`);
      assert.ok(
        !rx.test(src),
        `${label(file)} references \`${g}\`.\n` +
          `  It does not exist on React Native (or does not mean the same thing there).\n` +
          `  Take the value as an argument instead, so each platform supplies its own.`,
      );
    }
  }
});

test("nothing reaches back out of the package", () => {
  // A relative import that climbs past the package root would tie the shared core to one app's
  // directory layout — and would resolve at type-check time while failing in the Expo bundler,
  // which is the worst combination of symptoms to debug.
  for (const file of SOURCE) {
    for (const spec of importSpecifiers(readFileSync(file, "utf8"))) {
      if (!spec.startsWith(".")) continue;
      const target = join(file, "..", spec);
      assert.ok(
        relative(CORE, target).split(sep)[0] !== "..",
        `${label(file)} imports "${spec}", which escapes @ih/core.`,
      );
    }
  }
});
