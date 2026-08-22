// React must resolve to one copy for every importer in the bundle.
//
// This is guarded because the npm tree genuinely contains two Reacts, on purpose and correctly:
// `mobile/package.json` pins `react: "19.1.0"` (Expo SDK 54's pinned version) while every transitive
// dependency asks for `^19`, so npm hoists 19.2.8 to the repo root and nests 19.1.0 under mobile/.
// Nothing about that layout is wrong. What matters is which one the BUNDLER picks, and it has to
// pick the same one for the app's files and for `react-native` — which lives at the root and would
// otherwise find the root copy on the first step of an ordinary node-style lookup.
//
// Two copies is not a subtle bug. React's hooks dispatcher is module-level mutable state that the
// renderer sets on the copy it imported; a hook called through the other copy reads `null`. On a
// device this arrived as `TypeError: Cannot read property 'useState' of null` in the root layout,
// after `useColorScheme` on the line above had worked — React 19's production form of "Invalid hook
// call", with no mention of React, versions, or the monorepo anywhere in it.
//
// It cost a cloud build, an emulator install and a logcat dump to find. It costs milliseconds here.
//
// The fix was confirmed at the bundle, not just at the config. `expo export --platform android` over
// the same tree, changing nothing but this one resolver setting:
//
//   without the pin   the Hermes bundle contains the version strings 19.1.0 AND 19.2.8
//   with the pin      19.1.0 only
//
// That check is not run here — it needs a full export, which is minutes rather than milliseconds —
// but it is the reason the assertions below are worth trusting, and it is how to re-verify if this
// ever regresses in a way the origin check does not catch.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { join } from "node:path";

const require_ = createRequire(import.meta.url);
const MOBILE = join(import.meta.dirname, "..");

// Loading metro.config.js pulls in expo/metro-config, so skip rather than fail when dependencies are
// not installed — a bare checkout should not report this as a defect in the config.
let config: { resolver?: { resolveRequest?: unknown } } | null = null;
try {
  config = require_(join(MOBILE, "metro.config.js")) as typeof config;
} catch {
  config = null;
}

/**
 * Call the configured `resolveRequest` with a stub for Metro's default resolver, and report the
 * origin it was ultimately asked to resolve from.
 *
 * The origin is the whole question: Metro resolves node-style from the importing file first and only
 * consults `nodeModulesPaths` when that fails — which it never does for a package sitting next to
 * `react` in the root `node_modules`. So a config that normalises the origin gives every importer
 * the same copy, and one that does not gives them whichever is nearest.
 */
function originUsedFor(moduleName: string, importer: string): string {
  const resolveRequest = config?.resolver?.resolveRequest as
    | ((ctx: unknown, name: string, platform: string | null) => unknown)
    | undefined;
  assert.ok(resolveRequest, "metro.config.js must set resolver.resolveRequest");
  let seen = "";
  const context = {
    originModulePath: importer,
    resolveRequest: (ctx: { originModulePath: string }) => {
      seen = ctx.originModulePath;
      return { type: "sourceFile", filePath: "/stub" };
    },
  };
  resolveRequest(context, moduleName, "android");
  return seen;
}

const FROM_APP = join(MOBILE, "app", "_layout.tsx");
const FROM_HOISTED = join(MOBILE, "..", "node_modules", "react-native", "index.js");

test("react resolves from the same origin for the app and for a hoisted package", { skip: !config }, () => {
  assert.equal(
    originUsedFor("react", FROM_APP),
    originUsedFor("react", FROM_HOISTED),
    "react must resolve to one copy — the app and react-native are asking from different directories",
  );
});

test("the react subpaths are pinned too, not just the bare specifier", { skip: !config }, () => {
  // `react/jsx-runtime` is what compiled JSX imports. A bundle that pinned `react` but not its
  // subpaths would load the dispatcher from one copy and the JSX factory from another.
  for (const sub of ["react/jsx-runtime", "react/jsx-dev-runtime", "react/compiler-runtime"]) {
    assert.equal(
      originUsedFor(sub, FROM_APP),
      originUsedFor(sub, FROM_HOISTED),
      `${sub} must resolve to one copy`,
    );
  }
});

test("react-native is pinned as well", { skip: !config }, () => {
  assert.equal(originUsedFor("react-native", FROM_APP), originUsedFor("react-native", FROM_HOISTED));
});

test("an ordinary module is left alone", { skip: !config }, () => {
  // The rewrite must be surgical. @ih/core resolves its own dependency (axios) through the root
  // fallback, and rewriting every origin to mobile/ would change how unrelated packages resolve.
  assert.equal(
    originUsedFor("axios", FROM_HOISTED),
    FROM_HOISTED,
    "only the React singletons should have their resolution origin rewritten",
  );
});
