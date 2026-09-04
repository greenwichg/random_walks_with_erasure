// One search, two shells — pinned at the seam where they could become two searches.
//
// The desktop header expands its own field; the phone opens a full-screen overlay. Everything that
// decides what search MEANS is in search-results.tsx and shared. What this file guards is that the
// sharing stays real: a second `useSearch` call, a second results list, or a modal creeping back
// into the header are each how a "small" change turns one behaviour into two.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const WEB = join(import.meta.dirname, "..");
const read = (...p: string[]) => readFileSync(join(WEB, ...p), "utf8");
const INLINE = read("components", "layout", "header-search.tsx");
const OVERLAY = read("components", "layout", "search-command.tsx");
const HEADER = read("components", "layout", "header.tsx");
const SHARED = read("components", "layout", "search-results.tsx");

test("both shells run the same search", () => {
  for (const [name, src] of [["header field", INLINE], ["overlay", OVERLAY]] as const) {
    assert.ok(/useSearchLauncher/.test(src), `${name} must use the shared launcher`);
    assert.ok(/SearchResultList/.test(src), `${name} must render the shared result list`);
    assert.ok(!/useSearch\(/.test(src), `${name} must not call the search endpoint itself`);
  }
  assert.ok(/useSearch\(/.test(SHARED), "the shared launcher is where the endpoint is called");
});

test("the desktop field is not a modal", () => {
  // The whole point of the change: the page stays visible, so nothing here may open a dialog,
  // dim the page or lock its scroll.
  // `overflow-hidden` is fine and present — it is what rounds the results panel's corners. What
  // must never appear is a dialog, a full-screen layer over the page, or a lock on its scroll.
  for (const forbidden of ["Sheet", "Dialog", 'role="dialog"', "backdrop", "fixed inset-0", "body.style.overflow"]) {
    assert.ok(!INLINE.includes(forbidden), `the header field must not ${forbidden}`);
  }
  assert.ok(/document.addEventListener\("pointerdown"/.test(INLINE), "a press outside must close it");
  assert.ok(/e.key === "Escape"/.test(INLINE), "Escape must close it");
  assert.ok(/autoFocus/.test(INLINE), "the field must be ready to type");
});

test("the shortcut reaches whichever shell this viewport has", () => {
  assert.ok(/if \(desktop\) setInlineSearch/.test(HEADER), "⌘K must open the field on desktop");
  assert.ok(/else setSearchOpen/.test(HEADER), "…and the overlay below `lg`");
  assert.ok(/<SearchCommand /.test(HEADER), "the overlay must still be mounted for that path");
});
