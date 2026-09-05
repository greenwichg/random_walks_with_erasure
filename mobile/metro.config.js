// Metro, taught about the monorepo.
//
// Three settings, and all three are required rather than optional — without them the app fails in
// ways that point nowhere near the cause:
//
//   watchFolders     Metro only watches the project directory by default. @ih/core lives outside
//                    it, so an edit there would not trigger a reload, and a cold start would report
//                    the module as missing rather than as unwatched.
//
//   nodeModulesPaths npm workspaces hoist most packages to the repo root and nest only what
//                    conflicts. This makes the repo root a fallback for anything not found under
//                    mobile/ — which is how @ih/core's own dependency (axios) resolves.
//
//   resolveRequest   pins React to ONE copy for every importer. See below; this is the setting that
//                    took a crash on a real device to discover.
//
// `disableHierarchicalLookup` is deliberately NOT set: the fallback to the root is load-bearing.
const { getDefaultConfig } = require("expo/metro-config");
const path = require("node:path");

const projectRoot = __dirname;
const workspaceRoot = path.resolve(projectRoot, "..");

const config = getDefaultConfig(projectRoot);

config.watchFolders = [workspaceRoot];
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, "node_modules"),
  path.resolve(workspaceRoot, "node_modules"),
];

/**
 * Packages that must exist exactly once in the bundle.
 *
 * React holds module-level mutable state — the hooks dispatcher lives on `ReactSharedInternals`, and
 * the renderer sets it on the copy IT imported. A second copy has a dispatcher that is never set, so
 * a hook called through it reads `null`. Two copies is not a slow app or a subtle bug; it is a hard
 * crash on the first hook.
 */
const SINGLETONS = /^(react|react-dom|react-native)(\/.*)?$/;

/**
 * Resolve React as if every request came from `mobile/`, whoever actually asked.
 *
 * `nodeModulesPaths` alone does NOT do this, which is the trap. Metro resolves node-style first —
 * walking up from the importing file — and only falls back to `nodeModulesPaths` when that fails. It
 * never fails for `react-native`, because `react-native` is hoisted to `<root>/node_modules/` and
 * finds the sibling `<root>/node_modules/react` on the first step. So the app's files got
 * `mobile/node_modules/react` and React Native got the root's, and the bundle carried both.
 *
 * They were different versions, too, for a reason worth knowing: `mobile/package.json` pins
 * `react: "19.1.0"` exactly (Expo SDK 54's pinned version), while every transitive dependency asks
 * for `^19`. npm hoists the newest match — 19.2.8 — to the root and nests 19.1.0 under mobile/. The
 * pin is correct and the nesting is correct; what was missing was telling the bundler which one wins.
 *
 * The symptom on a device was `TypeError: Cannot read property 'useState' of null` in the root
 * layout — React 19's production form of "Invalid hook call". `useColorScheme` on the line above it
 * worked fine, because React Native's own hooks go through React Native's copy. Nothing in the
 * message mentions React, versions, or the monorepo.
 *
 * Only the search ORIGIN is rewritten; Metro still does the resolving, so platform extensions
 * (`.android.js`, `.ios.js`) and package `exports` maps keep working normally.
 */
/**
 * The parity validation harness (`scripts/validation/`; its report is docs/MOBILE_PARITY.md once the
 * pass completes) renders the app through react-native-web to put it beside the mobile web app. `expo-secure-store` is an empty module on that platform, so the harness
 * gets a localStorage stand-in — on the WEB platform only. Android and iOS bundles never see it;
 * `react-dom` joins the singletons for the same reason the others are there (one React per bundle).
 */
const WEB_ONLY_SUBSTITUTES = {
  "expo-secure-store": path.resolve(projectRoot, "scripts", "validation", "secure-store.web.js"),
};

const originalResolveRequest = config.resolver.resolveRequest;
config.resolver.resolveRequest = (context, moduleName, platform) => {
  const resolve = originalResolveRequest ?? context.resolveRequest;
  if (platform === "web" && WEB_ONLY_SUBSTITUTES[moduleName]) {
    return { type: "sourceFile", filePath: WEB_ONLY_SUBSTITUTES[moduleName] };
  }
  if (SINGLETONS.test(moduleName)) {
    return resolve({ ...context, originModulePath: __filename }, moduleName, platform);
  }
  return resolve(context, moduleName, platform);
};

// @ih/core ships TypeScript source with no build step (its package.json maps subpath exports
// straight at the .ts files), which is the same contract `next build` consumes on the web. Metro
// transpiles TypeScript natively, so nothing extra is needed — but the extension has to be in the
// resolver's list for `@ih/core/logic/coverage` to find `coverage.ts`.
config.resolver.sourceExts = Array.from(new Set([...config.resolver.sourceExts, "ts", "tsx"]));

module.exports = config;
