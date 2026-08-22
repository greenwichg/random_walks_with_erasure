// The shims are gone. This is what stops them growing back.
//
// For one commit, every module that moved to @ih/core left a one-line re-export at its old path, so
// that 175 call sites did not have to change in the same breath as 30 files moved. That was the
// right trade then and it is a liability now: a shim is an import path that works, and an import
// path that works is one somebody will use. The next `@/lib/coverage` would resolve to nothing —
// but the next `web/lib/coverage.ts`, recreated by someone who remembered it being there, would
// resolve to a SECOND copy of a shared module, and web and mobile would drift a rule apart without
// a single error anywhere.
//
// So this asserts the absence: the paths that moved must stay moved.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

const WEB = join(import.meta.dirname, "..");
const CORE = join(WEB, "..", "packages", "core");

/**
 * Paths that moved to @ih/core, and must not reappear under web/.
 *
 * Written out rather than derived from the core package, because the point is to pin the specific
 * files that were deleted. A rule computed from what happens to be in @ih/core today would silently
 * stop covering a file the day someone moved it back.
 */
const MOVED = [
  "types/domain.ts",
  "services/index.ts",
  "services/api.ts",
  "mock/data.ts",
  "mock/onboarding.ts",
  "mock/publishers.ts",
  ...[
    "analysis-presentation", "auth-decision", "bar-items", "calendar-grid", "chart-axis",
    "coach-presentation", "countries", "country-partition", "coverage", "discover-order",
    "discover-params", "engine-fallback", "framing", "hero-copy", "history-insights", "home",
    "i18n-core", "metrics", "nav", "notification-kinds", "political", "publisher-logo",
    "rec-presentation", "request-params", "settings-diff", "story-timeline", "story-wire-keys",
  ].map((n) => `lib/${n}.ts`),
];

/** Web modules that legitimately kept a name close to a moved one — the platform halves of a split. */
const SPLIT_HALVES = [
  "lib/metric-icons.ts",   // the lucide icons; the metric table is shared
  "lib/nav-icons.ts",      // ditto for navigation
  "lib/active-lang.ts",    // reads <html lang>; the i18n resolver is shared
  "lib/notifications.ts",  // a NotificationPresentation carries a LucideIcon by definition
  "lib/onboarding.ts",     // the localStorage stash; the predicate is shared
  "lib/record-read.ts",    // sendBeacon transport; the payload is shared
];

test("no moved module has reappeared under web/", () => {
  for (const rel of MOVED) {
    assert.ok(
      !existsSync(join(WEB, rel)),
      `web/${rel} is back.\n` +
        `  It lives in @ih/core now — recreating it here makes a second copy of a shared rule, and\n` +
        `  nothing would report the day web and mobile started disagreeing about it.\n` +
        `  If the module genuinely needs a web-only half, split it (see lib/metric-icons.ts) rather\n` +
        `  than restoring the whole file.`,
    );
  }
});

test("the split halves that DO belong to web are still here", () => {
  // The inverse failure: someone tidying up decides these look like leftovers and deletes them,
  // taking the icons or the storage with them. Naming them is what makes them deliberate.
  for (const rel of SPLIT_HALVES) {
    assert.ok(existsSync(join(WEB, rel)), `web/${rel} is missing — it is the web half of a split`);
  }
});

/** Every .ts/.tsx under web/, excluding build output and dependencies. */
function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".next" || entry === ".e2e-tmp") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

test("nothing imports a retired shim path", () => {
  // Belt and braces over the test above: a stale import would already fail to resolve, but it would
  // fail at BUILD time with a module-not-found, which is a worse place to learn it than here.
  const retired = MOVED.map((rel) => "@/" + rel.replace(/\.ts$/, ""));
  retired.push("@/services", "@/mock/data");
  for (const file of sources(WEB)) {
    const src = readFileSync(file, "utf8");
    for (const spec of retired) {
      const rx = new RegExp(`from\\s+["']${spec.replace(/[/\\]/g, "\\$&")}["']`);
      assert.ok(
        !rx.test(src),
        `${relative(WEB, file).split(sep).join("/")} imports "${spec}", which no longer exists.\n` +
          `  Import it from @ih/core instead.`,
      );
    }
  }
});

test("@ih/core is declared as a dependency, not merely resolved by the workspace", () => {
  // npm links a workspace into node_modules only for packages that ASK for it. Without this line,
  // `npm ci --workspace web` produces a tree with no @ih/core in it — which is exactly how the
  // production image failed the first time the Dockerfile was tested.
  const pkg = JSON.parse(readFileSync(join(WEB, "package.json"), "utf8"));
  assert.ok(pkg.dependencies?.["@ih/core"], "web/package.json must list @ih/core in dependencies");
});

test("the core package is actually there (the guard is not passing on an empty scan)", () => {
  assert.ok(existsSync(join(CORE, "domain", "types.ts")), "packages/core/domain/types.ts is missing");
  assert.ok(existsSync(join(CORE, "guard.test.ts")), "packages/core/guard.test.ts is missing");
});
