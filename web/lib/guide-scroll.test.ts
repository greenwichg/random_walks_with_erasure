import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

/**
 * The Guide (route `/coach`) is a fixed-height column whose transcript scrolls inside itself, so
 * it drew a scrollbar inset from the window edge. That scrollbar is now hidden — and hiding a
 * scrollbar removes both an affordance and, in some browsers, the only way to scroll without a
 * mouse: Safari and pre-127 Chrome do not put a scrollable `<div>` in the tab order by themselves.
 *
 * These two facts have to travel together. Someone tidying "an unused tabIndex" off the transcript
 * would leave a pane a keyboard user can neither reach nor see the state of, and nothing else in
 * the build would notice. This test is what notices.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, "..", "app", "(app)", "coach", "page.tsx"), "utf-8");

/** The transcript element: from its `ref={scrollRef}` to the end of its opening tag. */
function transcriptTag(): string {
  const at = SRC.indexOf("ref={scrollRef}");
  assert.ok(at >= 0, "the Guide transcript must still be the ref'd scroll container");
  const open = SRC.lastIndexOf("<div", at);
  const close = SRC.indexOf(">", at);
  assert.ok(open >= 0 && close > open, "could not read the transcript's opening tag");
  return SRC.slice(open, close + 1);
}

test("the Guide transcript hides its scrollbar in every engine", () => {
  const tag = transcriptTag();
  // Firefox reads `scrollbar-width`; Chrome, Edge and Safari read the `::-webkit-scrollbar`
  // pseudo-element. Only one of the two leaves a visible bar on half the browsers in use.
  assert.match(tag, /\[scrollbar-width:none\]/, "no Firefox rule — the bar stays there");
  assert.match(tag, /\[&::-webkit-scrollbar\]:hidden/, "no WebKit/Blink rule — the bar stays there");
});

test("hiding the bar did not hide the content: the transcript still scrolls", () => {
  // The request was to remove the scrollbar, never the scrolling.
  assert.match(transcriptTag(), /overflow-y-auto/, "the transcript must still scroll");
});

test("a hidden scrollbar comes with a keyboard route and a name", () => {
  const tag = transcriptTag();
  assert.match(tag, /tabIndex=\{0\}/, "a scroller no browser focuses is unreachable by keyboard");
  assert.match(tag, /role="log"/, "the transcript needs a role, so its new replies are announced");
  assert.match(tag, /aria-label=/, "a focusable region with no name is an unlabelled stop");
});
