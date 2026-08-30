import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { PLACEHOLDER_HUES, placeholderHues, monogram } from "./placeholder-art.ts";

/** Circular hue distance in degrees, 0–180. */
const dist = (a: number, b: number) => {
  const d = Math.abs(a - b) % 360;
  return Math.min(d, 360 - d);
};

describe("PLACEHOLDER_HUES", () => {
  it("keeps every decorative hue ≥ 20° off the lean axis, in both themes", () => {
    // The lean tokens are the one colour vocabulary these exact cards already speak (LeanBadge,
    // spectrum bars). A placeholder wash a reader could parse as "blue outlet" / "red outlet"
    // would assert a politics the engine never did. Light and dark themes move lightness, not
    // hue, so checking the four hue anchors covers both.
    for (const lean of [214, 213, 356]) {
      for (const hue of PLACEHOLDER_HUES) {
        assert.ok(
          dist(hue, lean) >= 20,
          `decorative hue ${hue} sits within 20° of lean hue ${lean} — a reader could read it as a lean`,
        );
      }
    }
  });
});

describe("placeholderHues", () => {
  it("is deterministic — the same outlet is the same colour on every surface and every render", () => {
    assert.deepEqual(placeholderHues("The Guardian"), placeholderHues("The Guardian"));
    assert.deepEqual(placeholderHues("  The Guardian  "), placeholderHues("The Guardian"));
  });

  it("draws only from the curated wheel and pairs each base with a nearby companion", () => {
    for (const seed of ["Reuters", "AP", "The Hill", "Der Spiegel", "朝日新聞", ""]) {
      const { base, companion } = placeholderHues(seed);
      assert.ok((PLACEHOLDER_HUES as readonly number[]).includes(base), `${seed}: base off-wheel`);
      assert.equal(companion, (base + 40) % 360, `${seed}: companion must be the duotone step`);
    }
  });

  it("actually spreads real outlet names across the wheel — identity, not one tint for all", () => {
    // Not a uniformity proof — a smoke check that the hash isn't collapsing. 24 real-shaped
    // names must land on at least 5 of the 8 hues.
    const names = [
      "Reuters", "Associated Press", "The Guardian", "BBC News", "CNN", "Fox News",
      "The Hill", "Politico", "Axios", "NPR", "Al Jazeera", "Der Spiegel", "Le Monde",
      "El País", "The Times of India", "Sydney Morning Herald", "The Verge", "Wired",
      "USA Today", "Bloomberg", "Financial Times", "Deutsche Welle", "France 24", "Kyodo News",
    ];
    const used = new Set(names.map((n) => placeholderHues(n).base));
    assert.ok(used.size >= 5, `24 outlets landed on only ${used.size} hues — the hash is collapsing`);
  });
});

describe("monogram", () => {
  it("takes the first letters of up to two words", () => {
    assert.equal(monogram("The Hill"), "TH");
    assert.equal(monogram("Reuters"), "R");
    assert.equal(monogram("Süddeutsche Zeitung"), "SZ");
  });

  it("skips non-letter marks and never returns empty", () => {
    assert.equal(monogram("«Le Monde»"), "M"); // "«" leads word one and is filtered; "M" survives
    assert.equal(monogram(""), "?");
    assert.equal(monogram("—"), "?");
  });
});
