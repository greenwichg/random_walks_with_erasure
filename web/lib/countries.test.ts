import { test } from "node:test";
import assert from "node:assert/strict";
import { countryFlag, countryName, countryShortName, languageName } from "./countries.ts";

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
