// Screens must not pass an array style through expo-router's `asChild`.
//
// `<Link asChild>` renders the child through expo-router's `Slot`, which is Radix's `Slot`, whose
// `mergeProps` merges the style prop as:
//
//     overrideProps.style = { ...slotPropValue, ...childPropValue }
//
// React Native styles are usually ARRAYS — `style={[styles.cta, { backgroundColor: … }]}` is the
// idiom this codebase uses everywhere. Spreading an array into an object literal gives
// `{ "0": {...}, "1": {...} }`: numeric keys, no style properties, every rule dropped. expo-router's
// shim flattens the Slot's own style before the merge (ui/Slot.js) but not the child's, so an object
// style survives this and an array style does not.
//
// It reached a device as a sign-in button with `backgroundColor: "#543bce"`, 24pt padding and a 10pt
// radius rendering as nothing at all — white label, white screen, no button. It was still there and
// still tappable, so it did not crash, log, or fail a test. It just could not be seen.
//
// The ban is on `asChild`, not on the array: `StyleSheet.flatten([...])` at the call site would also
// be correct, but it has to be remembered at every call site forever, and forgetting it is invisible.
// `router.push(...)` on a plain `Pressable` has none of this, and `app/sign-in.tsx` already used it.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const MOBILE = join(import.meta.dirname, "..");

// Only the screen and component trees are scanned — deliberately NOT `lib/`, which is where this
// file lives. A guard that scans its own directory finds its own explanation of the thing it
// forbids and fails; that has happened four times in this repo, most recently in
// `lib/boundary.test.ts`, whose FORBIDDEN list contains the very string it searches for.
const ROOTS = ["app", "components"];

function screens(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) screens(full, out);
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

/** Source with comments stripped, so an explanation of the rule never trips the rule. */
function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n\r]*/g, "");
}

test("no screen uses `asChild`, which drops array styles without saying so", () => {
  const offenders: string[] = [];
  for (const root of ROOTS) {
    for (const file of screens(join(MOBILE, root))) {
      if (/\basChild\b/.test(code(readFileSync(file, "utf8")))) {
        offenders.push(relative(MOBILE, file));
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `asChild found in ${offenders.join(", ")}. It merges styles through Radix's Slot, which turns ` +
      `an array style into an object with numeric keys and silently drops every rule. Navigate with ` +
      `router.push(...) on a plain Pressable instead — see lib/slot-style.test.ts for the detail.`,
  );
});

test("the scanner actually reads the screens", () => {
  // Without this, a broken glob would make the test above pass by finding nothing — the failure mode
  // where a guard reports success because it never looked.
  const files = ROOTS.flatMap((r) => screens(join(MOBILE, r)));
  assert.ok(files.length >= 4, `expected to scan several screens, found ${files.length}`);
  assert.ok(
    files.some((f) => f.endsWith("index.tsx")),
    "the Recommendations screen must be among the scanned files",
  );
});
