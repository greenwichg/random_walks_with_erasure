// The editorial grid's one load-bearing rule, and the timeline control's.
//
// WHY A SOURCE TEST. Both defects here were invisible until a specific run of data arrived: a rail
// taller than the lead column, and a timeline longer than its collapsed window. Nothing in the
// unit suite renders a 3,500px rail, and the browser pass that measured it is not something CI
// runs. What CAN be pinned cheaply is the shape of the fix — the grid placement that caused the
// empty band, and the one-way button that caused the trapped panel — so a future edit that
// reintroduces either one fails here rather than on someone's screen.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const WEB = join(import.meta.dirname, "..");
const GRID = readFileSync(join(WEB, "components", "layout", "page-grid.tsx"), "utf8");
const INTEL = readFileSync(join(WEB, "components", "stories", "story-intelligence-panel.tsx"), "utf8");

test("the rail can never decide how tall the lead column's first row is", () => {
  // The lead and the rest of the lead column were two grid rows with the rail spanning both, so a
  // tall rail stretched row 1 and left a band of empty page under the hero — measured at 1,056px
  // with a forty-event timeline open. One cell, one flow, and the band cannot exist.
  assert.ok(!/row-span-2/.test(GRID), "a rail that spans rows inflates the row the lead sits in");
  assert.ok(!/lg:row-start-2/.test(GRID), "the lead column must not be split across two rows");
  assert.ok(/className="contents lg:col-span-8/.test(GRID),
    "lead + children must share one grid cell at `lg`, and dissolve into the flow below it");
  // …and the phone's order (hero → rail → the rest) survives that, which is the whole reason the
  // `lead` prop exists.
  assert.ok(/order-1/.test(GRID) && /order-2/.test(GRID) && /order-3/.test(GRID),
    "below `lg` the three blocks must still be explicitly ordered");
});

test("the timeline control reverses instead of disappearing", () => {
  assert.ok(/setExpanded\(\(v\) => !v\)/.test(INTEL), "the control must toggle, not latch open");
  assert.ok(/hiddenCount > 0 \|\| expanded/.test(INTEL),
    "it must stay rendered once expanded — otherwise nothing closes the panel");
  assert.ok(/aria-expanded=\{expanded\}/.test(INTEL) && /aria-controls="story-intel-timeline"/.test(INTEL),
    "the button must announce its state and name the region it controls");
  assert.ok(/expanded && "rotate-180"/.test(INTEL), "the chevron must carry the state");
  assert.ok(/storyIntel\.showFewerEvents/.test(INTEL), "the collapsed label needs its own copy");
});
