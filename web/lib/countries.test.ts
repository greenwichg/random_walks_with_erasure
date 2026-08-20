import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { countryFlag, countryFlagSrc, countryName, countryShortName, languageName, sortByCountryName } from "./countries.ts";

test("countryFlag: regional-indicator pair for a valid alpha-2 code, case-insensitive", () => {
  assert.equal(countryFlag("US"), "🇺🇸");
  assert.equal(countryFlag("gb"), "🇬🇧");
  assert.equal(countryFlag("QA"), "🇶🇦");
});

test("countryFlag: empty string for anything that is not an alpha-2 code", () => {
  assert.equal(countryFlag(""), "");
  assert.equal(countryFlag("USA"), "");
  assert.equal(countryFlag("U"), "");
  assert.equal(countryFlag("U1"), "");
});

test("countryName: localized display name in the requested language", () => {
  assert.equal(countryName("US"), "United States");
  assert.equal(countryName("US", "es"), "Estados Unidos");
  assert.equal(countryName("DE", "de"), "Deutschland");
  // lowercase input still resolves — callers pass whatever the API stored
  assert.equal(countryName("gb"), "United Kingdom");
});

test("countryName: falls back to the code, never a guess", () => {
  // not an alpha-2 shape → returned verbatim
  assert.equal(countryName("USA"), "USA");
  assert.equal(countryName(""), "");
  // a runtime without Intl.DisplayNames → uppercased code (the null-cache branch; the probe
  // lang is unique so the poisoned cache entry never collides with real lookups)
  const orig = Intl.DisplayNames;
  (Intl as { DisplayNames: unknown }).DisplayNames = function () {
    throw new RangeError("unsupported");
  };
  try {
    assert.equal(countryName("fr", "zz-noruntime"), "FR");
  } finally {
    (Intl as { DisplayNames: unknown }).DisplayNames = orig;
  }
});

test("countryShortName: recognisable short forms only; everything else keeps the full name", () => {
  assert.equal(countryShortName("US"), "USA");
  assert.equal(countryShortName("gb"), "UK");
  assert.equal(countryShortName("AE"), "UAE");
  // no overlay entry → identical to the full name (the badge then renders a single name)
  assert.equal(countryShortName("FR"), countryName("FR"));
  assert.equal(countryShortName("QA"), countryName("QA"));
});

test("languageName: localized language display name with code fallback", () => {
  assert.equal(languageName("en"), "English");
  assert.equal(languageName("en", "fr"), "anglais");
  assert.equal(languageName("ES", "es"), "español"); // case-insensitive input
  assert.equal(languageName("zz"), "zz"); // unknown → the code itself
  assert.equal(languageName("x"), "x"); // not an ISO shape → verbatim
});

// --------------------------------------------------------------------------- //
// Ordering — a list you scan for a known country has to be alphabetical.
// --------------------------------------------------------------------------- //
test("countries order by display name, not by ISO code", () => {
  // The codes would put IL before IR; the NAMES put Iran before Israel. Sorting the code is the
  // bug this exists to prevent — the reader is scanning names.
  assert.deepEqual(sortByCountryName(["IL", "IR", "US", "GB", "JP"]), ["IR", "IL", "JP", "GB", "US"]);
});

test("ordering is by name even when the input arrives ranked by something else", () => {
  // The Stories facets arrive keyed by story count; that order must not survive.
  const byStoryCount = ["US", "GB", "IN", "CN", "AU", "FR", "JP", "ES"];
  assert.deepEqual(
    sortByCountryName(byStoryCount),
    ["AU", "CN", "FR", "IN", "JP", "ES", "GB", "US"],
  );
});

test("the input is not mutated — facets are reused across refetches", () => {
  const codes = ["US", "AU"];
  const out = sortByCountryName(codes);
  assert.deepEqual(codes, ["US", "AU"], "the caller's array must be left alone");
  assert.deepEqual(out, ["AU", "US"]);
});

test("ties break on the code, so the order is total and stable", () => {
  // Two codes the runtime cannot name resolve to themselves; without the tiebreak their relative
  // order would depend on the engine's sort stability.
  const a = sortByCountryName(["ZZ", "ZY", "US"]);
  const b = sortByCountryName(["ZY", "ZZ", "US"]);
  assert.deepEqual(a, b);
});

test("an empty list and unknown codes degrade quietly", () => {
  assert.deepEqual(sortByCountryName([]), []);
  assert.deepEqual(sortByCountryName(["ZZ"]), ["ZZ"]);
});

// --------------------------------------------------------------------------- //
// Flags — artwork, not emoji, because Windows has no flag glyphs.
// --------------------------------------------------------------------------- //
test("a flag resolves to shipped artwork, not a platform glyph", () => {
  // The defect: `countryFlag` builds a regional-indicator PAIR and asks the platform to draw a
  // flag for it. Windows ships none, so every Windows browser rendered the two letters and the
  // same chip looked different on desktop and phone.
  assert.equal(countryFlagSrc("IL"), "/flags/il.svg");
  assert.equal(countryFlagSrc("us"), "/flags/us.svg", "case-insensitive: codes arrive upper-cased");
});

test("a non-region code yields no flag rather than a broken image", () => {
  for (const bad of ["", "U", "USA", "1A", "  ", "zz9"]) {
    assert.equal(countryFlagSrc(bad), "", `${JSON.stringify(bad)} must not produce a src`);
  }
});

test("the badge renders artwork, and degrades to the name when it cannot", () => {
  const src = readFileSync(new URL("../components/shared/country-badge.tsx", import.meta.url), "utf-8");
  assert.match(src, /countryFlagSrc\(/, "the chip must use shipped artwork");
  assert.doesNotMatch(src, /countryFlag\(/, "an emoji flag renders as bare letters on Windows");
  assert.match(src, /onError=/, "a missing file must fall back to the name, not a broken-image icon");
  assert.match(src, /alt=""/, "decorative: the country name beside it is the real text");
});

test("the flag artwork is built into public/, never committed", () => {
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf-8"));
  assert.match(pkg.scripts.build, /build:flags/, "the production build must copy the flags");
  assert.match(pkg.scripts["build:e2e"], /build:flags/, "…and so must the e2e build");
  assert.ok(pkg.dependencies["flag-icons"], "the artwork source is a real dependency, not vendored");
  const ignored = readFileSync(new URL("../.gitignore", import.meta.url), "utf-8");
  assert.match(ignored, /^\/public\/flags\/$/m, "1.9 MB of artwork does not belong in every clone");
});
