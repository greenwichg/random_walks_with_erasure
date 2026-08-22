/**
 * Unit tests for the i18n core (Commit 20). Runs with Node's built-in runner + type stripping:
 *
 *     node --test web/lib/i18n-core.test.ts
 *
 * Covers message lookup + the fallback chain, interpolation, supported-language normalization,
 * the resolver-type → explanation mapping, and locale formatting — plus a catalog **key-parity**
 * check across all five message files (the "no missing translation keys" guarantee).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  SUPPORTED,
  normalizeLang,
  interpolate,
  makeT,
  explanationKey,
  localizeExplanation,
  formatDate,
  formatCompact,
  timeAgo,
} from "./i18n-core.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const load = (lang: string) =>
  JSON.parse(readFileSync(join(HERE, "..", "..", "packages", "core", "i18n", "messages", `${lang}.json`), "utf8")) as Record<string, string>;

test("normalizeLang allows the five supported languages and falls back to English", () => {
  for (const l of SUPPORTED) assert.equal(normalizeLang(l), l);
  for (const bad of ["klingon", "", null, undefined, "EN", 42]) assert.equal(normalizeLang(bad), "en");
});

test("interpolate fills params and leaves unknown placeholders intact", () => {
  assert.equal(interpolate("Hi {name}", { name: "Sam" }), "Hi Sam");
  assert.equal(interpolate("Hi {name}", {}), "Hi {name}"); // never blanked
  assert.equal(interpolate("no params"), "no params");
});

test("makeT: active language wins, then English, then the key itself", () => {
  const es = load("es");
  const en = load("en");
  const t = makeT(es, en);
  assert.equal(t("nav.settings"), "Ajustes"); // active language
  const partial = makeT({ "only.here": "X" }, en);
  assert.equal(partial("nav.settings"), "Settings"); // falls back to English catalog
  const misses: string[] = [];
  const tm = makeT({}, {}, (k) => misses.push(k));
  assert.equal(tm("nope.key"), "nope.key"); // final fallback = the key
  assert.deepEqual(misses, ["nope.key"]);
});

test("explanationKey maps every resolver type/variant to a template key", () => {
  assert.equal(explanationKey({ type: "story_match", variant: "same_event" }),
    "explanation.story_match.same_event");
  assert.equal(explanationKey({ type: "story_match", variant: "follow_up" }),
    "explanation.story_match.follow_up");
  assert.equal(explanationKey({ type: "story_match", variant: "following" }),
    "explanation.story_match.following");
  assert.equal(explanationKey({ type: "topic_continuity", evidence: { crossCutting: true } }),
    "explanation.topic_continuity.perspective");
  assert.equal(explanationKey({ type: "topic_continuity", evidence: { crossCutting: false } }),
    "explanation.topic_continuity.outlet");
  assert.equal(explanationKey({ type: "new_publisher", evidence: { band: "never" } }),
    "explanation.new_publisher.never");
  assert.equal(explanationKey({ type: "new_publisher", evidence: { band: "rarely" } }),
    "explanation.new_publisher.rarely");
  assert.equal(explanationKey({ type: "bridge" }), "explanation.bridge");
  assert.equal(explanationKey({ type: "long_tail" }), "explanation.long_tail");
  assert.equal(explanationKey({ type: "coverage_breadth", evidence: { topic: "Politics" } }),
    "explanation.coverage_breadth.topic");
  assert.equal(explanationKey({ type: "coverage_breadth", evidence: {} }),
    "explanation.coverage_breadth.generic");
  assert.equal(explanationKey({ type: "unknown" }), "");
});

test("localizeExplanation builds the localized sentence from evidence, in the active language", () => {
  const t = makeT(load("es"), load("en"));
  const out = localizeExplanation(
    { type: "story_match", variant: "same_event", message: "EN fallback",
      evidence: { readPublisher: "Fox News", recPublisher: "CNN" } }, t);
  assert.equal(out, "Ya leíste esta noticia en Fox News. Así la cubrió CNN.");
  // unknown type → fall back to the server-provided English message (stays honest)
  assert.equal(localizeExplanation({ type: "mystery", message: "server said this" }, t),
    "server said this");
});

test("formatCompact and formatDate use the active language", () => {
  assert.equal(formatCompact(1200, "en"), "1.2K");
  assert.equal(typeof formatCompact(1200, "de"), "string");
  // Locale threading is proven via dates (full ICU for month names even on a small-ICU Node;
  // locale-specific compact-number separators need full ICU, exercised by the browser E2E).
  const iso = "2026-07-04T00:00:00Z";
  assert.notEqual(formatDate(iso, "fr", { month: "long" }), formatDate(iso, "en", { month: "long" }));
  assert.equal(formatDate("not-a-date", "en", { month: "long" }), "");
});

test("timeAgo uses localized unit words", () => {
  const t = makeT(load("es"), load("en"));
  const twoHrs = new Date(Date.now() - 2 * 3600_000).toISOString();
  assert.equal(timeAgo(twoHrs, "es", t), "hace 2 h");
  const now = new Date().toISOString();
  assert.equal(timeAgo(now, "es", t), "ahora mismo");
});

test("timeAgo renders nothing for an unknown date (C4: real articles never get a fabricated time)", () => {
  const t = makeT(load("en"), load("en"));
  assert.equal(timeAgo("", "en", t), "");
  assert.equal(timeAgo("not-a-date", "en", t), "");
});

test("all five catalogs share identical keys with non-empty values (no missing translations)", () => {
  const cats = Object.fromEntries(SUPPORTED.map((l) => [l, load(l)]));
  const enKeys = Object.keys(cats.en).sort();
  assert.ok(enKeys.length > 100, "en catalog should be substantial");
  for (const l of SUPPORTED) {
    assert.deepEqual(Object.keys(cats[l]).sort(), enKeys, `${l}.json key set differs from en.json`);
    for (const [k, v] of Object.entries(cats[l])) {
      assert.ok(typeof v === "string" && v.trim().length > 0, `${l}.json has an empty value for ${k}`);
    }
  }
});

test("interpolation placeholders match across every language (no dropped {params})", () => {
  const cats = Object.fromEntries(SUPPORTED.map((l) => [l, load(l)]));
  const ph = (s: string) => new Set([...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]));
  for (const key of Object.keys(cats.en)) {
    const base = [...ph(cats.en[key])].sort();
    for (const l of SUPPORTED) {
      assert.deepEqual([...ph(cats[l][key])].sort(), base, `${l}.json "${key}" has different {placeholders} than en`);
    }
  }
});
