// The one country-search matcher — shared by the For You, Preferred edition and Followed places
// pickers so the three cannot drift into different ideas of "matches".
//
// The load-bearing property is the diacritic fold: ICU's current names are Türkiye, Côte d'Ivoire,
// São Tomé, Åland — and a reader types the letters on their keyboard, not the accents. The empty
// query matching everything is equally load-bearing: it is the picker's browse state, and a matcher
// that returned nothing for "" would render every popover empty until the first keystroke.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fold, matchesCountry } from "./country-search.ts";

test("fold strips diacritics and case so keyboard letters match ICU names", () => {
  assert.equal(fold("Türkiye"), "turkiye");
  assert.equal(fold("Côte d'Ivoire"), "cote d'ivoire");
  assert.equal(fold("  ÅLAND "), "aland");
});

test("matches by localized name, by ISO code, and diacritic-insensitively", () => {
  assert.ok(matchesCountry("TR", "Türkiye", "tur"), "typed letters must reach the accented name");
  assert.ok(matchesCountry("TR", "Türkiye", "tü"), "the accented spelling still matches too");
  assert.ok(matchesCountry("DE", "Germany", "de"), "the ISO code is a valid way to search");
  assert.ok(!matchesCountry("DE", "Germany", "fra"), "an unrelated query must not match");
});

test("the empty query is the browse state — it matches everything", () => {
  assert.ok(matchesCountry("US", "United States", ""));
  assert.ok(matchesCountry("US", "United States", "   "), "whitespace folds to empty");
});
