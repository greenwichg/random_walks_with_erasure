// Metro, taught about the monorepo.
//
// Two settings, and both are required rather than optional — without them the app fails in ways
// that point nowhere near the cause:
//
//   watchFolders     Metro only watches the project directory by default. @ih/core lives outside
//                    it, so an edit there would not trigger a reload, and a cold start would report
//                    the module as missing rather than as unwatched.
//
//   nodeModulesPaths npm workspaces hoist most packages to the repo root and nest only what
//                    conflicts. This tree has TWO Reacts on purpose — web pins 18.3, Expo 57 ships
//                    19 — so `mobile/node_modules/react` exists and the root has a different one.
//                    Listing mobile's own directory FIRST is what makes React resolve to 19 here.
//                    Getting this wrong produces "Invalid hook call" at runtime, which reads like a
//                    bug in the component that happened to render first.
//
// `disableHierarchicalLookup` is deliberately NOT set: the fallback to the root is how @ih/core's
// own dependency (axios) resolves.
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

// @ih/core ships TypeScript source with no build step (its package.json maps subpath exports
// straight at the .ts files), which is the same contract `next build` consumes on the web. Metro
// transpiles TypeScript natively, so nothing extra is needed — but the extension has to be in the
// resolver's list for `@ih/core/logic/coverage` to find `coverage.ts`.
config.resolver.sourceExts = Array.from(new Set([...config.resolver.sourceExts, "ts", "tsx"]));

module.exports = config;
