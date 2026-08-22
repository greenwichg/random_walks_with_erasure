import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  logoCandidates,
  requiredPixels,
  isTooLowRes,
  nextCandidate,
  hostIconCandidates,
} from "./publisher-logo.ts";

describe("logoCandidates", () => {
  it("puts the chosen logo first and its alternates after", () => {
    assert.deepEqual(
      logoCandidates("https://x/commons.png", ["https://x/apple-touch-icon.png", "https://x/favicon.ico"]),
      ["https://x/commons.png", "https://x/apple-touch-icon.png", "https://x/favicon.ico"],
    );
  });

  it("drops duplicates so a failed URL is never retried in the same walk", () => {
    // The enriched tier can name the same asset the site-icon tier would; retrying it wastes a
    // request and, worse, makes the walk look like it has more options than it does.
    assert.deepEqual(logoCandidates("https://x/a.png", ["https://x/a.png", "https://x/b.png"]), [
      "https://x/a.png",
      "https://x/b.png",
    ]);
  });

  it("survives a publisher with no logo at all", () => {
    assert.deepEqual(logoCandidates(null, null), []);
    assert.deepEqual(logoCandidates(undefined, []), []);
  });
});

describe("requiredPixels", () => {
  it("scales the box by the device pixel ratio", () => {
    assert.equal(requiredPixels(36, 1), 36);
    assert.equal(requiredPixels(36, 2), 72);
  });

  it("caps at 3x", () => {
    // Beyond 3x the extra pixels are invisible, and demanding them would reject good icons on
    // exactly the devices where nobody could tell.
    assert.equal(requiredPixels(36, 4), 108);
    assert.equal(requiredPixels(36, 10), 108);
  });

  it("never demands less than the CSS size", () => {
    assert.equal(requiredPixels(36, 0.5), 36);
  });
});

describe("isTooLowRes", () => {
  it("rejects the favicon that started this — 16px in a 36px box at 2x", () => {
    assert.equal(isTooLowRes(16, 36, 2), true);
    assert.equal(isTooLowRes(32, 36, 2), true);
  });

  it("accepts an Apple touch icon in the same box", () => {
    assert.equal(isTooLowRes(180, 36, 2), false);
  });

  it("accepts a Commons render", () => {
    assert.equal(isTooLowRes(320, 36, 2), false);
  });

  it("tolerates a quarter under rather than demanding an exact match", () => {
    // 64px in a box needing 72 is a difference nobody can see, and publishers ship power-of-two
    // icons that land just under common box sizes. Rejecting those would send perfectly good
    // marks to the glyph.
    assert.equal(isTooLowRes(64, 36, 2), false);
    assert.equal(isTooLowRes(48, 36, 2), true);
  });

  it("passes a 16px favicon at badge size, where it is genuinely adequate", () => {
    // The inline badge is 14px. The same asset that is unusable in the header is fine here, so
    // the rule has to be box-relative rather than a blanket ban on small icons.
    assert.equal(isTooLowRes(16, 14, 1), false);
  });

  it("treats an unloaded image as not-a-size-judgement", () => {
    // naturalWidth 0 means "not decoded yet" or "failed" — the error path owns that, not this.
    assert.equal(isTooLowRes(0, 36, 2), false);
  });

  it("never demotes an SVG, whose naturalWidth is a viewBox and not a quality", () => {
    // A curated logo.svg authored at 24x24 renders pin-sharp at any size. Judging it by the same
    // rule as a bitmap would drop it to the favicon underneath — replacing the best asset we have
    // with the blurriest one, which is precisely backwards.
    assert.equal(isTooLowRes(24, 36, 2, "https://x/logo.svg"), false);
    assert.equal(isTooLowRes(24, 36, 2, "https://x/logo.svgz"), false);
    assert.equal(isTooLowRes(24, 36, 2, "https://x/logo.svg?v=2"), false);
  });

  it("still judges bitmaps that merely mention svg in the path", () => {
    // The guard keys on the extension, not on the string appearing anywhere in the URL.
    assert.equal(isTooLowRes(16, 36, 2, "https://x/svg-assets/favicon.ico"), true);
  });

  it("judges by size when no URL is supplied", () => {
    assert.equal(isTooLowRes(16, 36, 2), true);
  });
});

describe("nextCandidate", () => {
  const list = ["a", "b", "c"];

  it("starts at the first candidate", () => {
    assert.equal(nextCandidate(list, null), "a");
  });

  it("advances one step at a time", () => {
    assert.equal(nextCandidate(list, "a"), "b");
    assert.equal(nextCandidate(list, "b"), "c");
  });

  it("returns null when exhausted, so the caller shows the glyph", () => {
    // Exhaustion is a real outcome. A publisher exposing no usable icon should get the monogram,
    // not a stretched 16px favicon — showing nothing beats showing something misleadingly bad.
    assert.equal(nextCandidate(list, "c"), null);
  });

  it("returns null for an unknown current value rather than restarting the walk", () => {
    assert.equal(nextCandidate(list, "zzz"), null);
  });

  it("handles an empty list", () => {
    assert.equal(nextCandidate([], null), null);
  });
});

describe("hostIconCandidates", () => {
  it("derives the engine's three icon paths from an article URL, largest first", () => {
    // Mirrors media._ICON_PATHS order: the 180x180 Apple touch icon leads, the 16-32px
    // favicon.ico is the last resort — the same rule the engine's pick_best_logo ships.
    assert.deepEqual(hostIconCandidates("https://www.foxnews.com/politics/x?utm=1"), [
      "https://www.foxnews.com/apple-touch-icon.png",
      "https://www.foxnews.com/apple-touch-icon-precomposed.png",
      "https://www.foxnews.com/favicon.ico",
    ]);
  });

  it("yields nothing for junk, relative, or absent URLs — never a guessed host", () => {
    assert.deepEqual(hostIconCandidates("not a url"), []);
    assert.deepEqual(hostIconCandidates("/relative/path"), []);
    assert.deepEqual(hostIconCandidates(""), []);
    assert.deepEqual(hostIconCandidates(null), []);
    assert.deepEqual(hostIconCandidates(undefined), []);
  });

  it("keeps the host exactly as the article states it (no www-stripping guesses)", () => {
    assert.equal(
      hostIconCandidates("https://apnews.com/article/1")[0],
      "https://apnews.com/apple-touch-icon.png",
    );
  });
});
