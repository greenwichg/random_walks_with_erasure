#!/usr/bin/env node
/**
 * check-i18n — the build-gating i18n validator (Commit 20.1, Requirement 6).
 *
 * Fails (exit 1) if any of the following is wrong, so a broken catalog can never ship:
 *   1. Key parity      — all 5 catalogs (en/es/fr/de/pt) share an identical key set.
 *   2. No empty values — no key maps to "" / whitespace in any language.
 *   3. Placeholder parity — a key's `{name}` interpolation tokens are identical across languages
 *      (so a translation can't silently drop a `{publisher}` a component depends on).
 *   4. Explanation coverage — every resolver (type, variant) from explanationKey() has a template.
 *   5. No unused keys  — every catalog key is referenced in source (literally, or under a
 *      documented dynamic-key prefix built via template literals).
 *
 * Run: `node scripts/check-i18n.mjs` (wired into `npm run build` via `npm run check:i18n`).
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(__dirname, "..");
const MSG = path.join(WEB, "messages");
const LANGS = ["en", "es", "fr", "de", "pt"];

/**
 * Dynamic key families constructed at runtime via template literals — a key under one of these
 * prefixes counts as "used" even though its full string never appears verbatim in source. Each is
 * annotated with where it is built so the allowlist stays auditable.
 */
const DYNAMIC_PREFIXES = [
  "explanation.",          // explanationKey() in lib/i18n-core.ts
  "emotion.",              // t(`emotion.${key}`) — badges, analytics, attention profile, history
  "filter.",              // t(`filter.${leanBucket}`) — lean labels in badges/why-drawer/stories
  "metric.",               // t(`metric.${key}.label|short|tooltip|description`)
  "band.",                 // t(`band.${label}`) — health band chip
  "rec.strategy.",         // t(`rec.strategy.${value}`) — recommendation filter tabs
  "storyIntel.lifecycle.", // t(`storyIntel.lifecycle.${lifecycle}`)
  "storyIntel.momentum.",  // t(`storyIntel.momentum.${state}`)
  "freshness.",            // t(`freshness.${band}`) — freshness badge
];

/**
 * Required resolver explanation templates — mirrors the switch in explanationKey() (lib/i18n-core.ts).
 * Every supported (type, variant) MUST have a catalog template, or a recommendation would fall back
 * to raw English server prose.
 */
/**
 * Commit 23 — every semantic part the resolver can emit MUST have a template, or a structured
 * explanation would silently fall back to the English message. Mirrors READER_TEMPLATES /
 * CONTRIBUTION_TEMPLATES in lib/rec-presentation.ts.
 */
const REQUIRED_PART_KEYS = [
  "rec.reader.read_story_from",
  "rec.reader.following_story",
  "rec.reader.never_read_publisher",
  "rec.reader.rarely_read_publisher",
  "rec.reader.top_topic",
  "rec.reader.political_lean_left",
  "rec.reader.political_lean_right",
  "rec.contribution.covered_same_story",
  "rec.contribution.story_update",
  "rec.contribution.story_coverage",
  "rec.contribution.add_new_publisher",
  "rec.contribution.more_topic_coverage",
  "rec.contribution.other_side_perspective",
  "rec.contribution.rare_in_feeds",
];

const REQUIRED_EXPLANATION_KEYS = [
  "explanation.story_match.same_event",
  "explanation.story_match.follow_up",
  "explanation.story_match.following",
  "explanation.topic_continuity.perspective",
  "explanation.topic_continuity.outlet",
  "explanation.new_publisher.never",
  "explanation.new_publisher.rarely",
  "explanation.bridge",
  "explanation.long_tail",
  "explanation.coverage_breadth.topic",
  "explanation.coverage_breadth.generic",
];

const errors = [];
const fail = (m) => errors.push(m);

// ---- load catalogs ----
const cats = {};
for (const lang of LANGS) {
  const p = path.join(MSG, `${lang}.json`);
  if (!fs.existsSync(p)) {
    fail(`missing catalog: messages/${lang}.json`);
    continue;
  }
  try {
    cats[lang] = JSON.parse(fs.readFileSync(p, "utf8"));
  } catch (e) {
    fail(`messages/${lang}.json is not valid JSON: ${e.message}`);
  }
}
const en = cats.en ?? {};
const enKeys = new Set(Object.keys(en));

// ---- 1. key parity ----
for (const lang of LANGS) {
  if (lang === "en" || !cats[lang]) continue;
  const k = new Set(Object.keys(cats[lang]));
  const missing = [...enKeys].filter((x) => !k.has(x));
  const extra = [...k].filter((x) => !enKeys.has(x));
  if (missing.length)
    fail(`${lang}.json is missing ${missing.length} key(s): ${missing.slice(0, 10).join(", ")}${missing.length > 10 ? " …" : ""}`);
  if (extra.length)
    fail(`${lang}.json has ${extra.length} extra key(s): ${extra.slice(0, 10).join(", ")}${extra.length > 10 ? " …" : ""}`);
}

// ---- 2. no empty values ----
for (const lang of LANGS) {
  for (const [key, val] of Object.entries(cats[lang] ?? {})) {
    if (typeof val !== "string" || val.trim() === "") fail(`${lang}.json has an empty value for "${key}"`);
  }
}

// ---- 3. placeholder parity ----
const placeholders = (s) => new Set([...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]));
for (const key of enKeys) {
  const base = placeholders(en[key]);
  for (const lang of LANGS) {
    if (lang === "en") continue;
    const v = cats[lang]?.[key];
    if (v === undefined) continue; // already reported by parity
    const set = placeholders(v);
    const missing = [...base].filter((x) => !set.has(x));
    const extra = [...set].filter((x) => !base.has(x));
    if (missing.length || extra.length)
      fail(`placeholder mismatch for "${key}" (${lang}): en{${[...base].join(",")}} vs ${lang}{${[...set].join(",")}}`);
  }
}

// ---- 4. explanation + structured-part template coverage ----
for (const k of REQUIRED_EXPLANATION_KEYS) {
  if (!enKeys.has(k)) fail(`missing required explanation template: "${k}"`);
}
for (const k of REQUIRED_PART_KEYS) {
  if (!enKeys.has(k)) fail(`missing required part template: "${k}"`);
}

// ---- 5. unused keys (scan source) ----
const SRC_DIRS = ["app", "components", "lib", "hooks", "services", "types"];
const sources = [];
const walk = (dir) => {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, ent.name);
    if (ent.isDirectory()) {
      if (ent.name === "node_modules" || ent.name === ".next") continue;
      walk(full);
    } else if (/\.(ts|tsx)$/.test(ent.name) && !/\.test\.(ts|tsx)$/.test(ent.name)) {
      sources.push(full);
    }
  }
};
for (const d of SRC_DIRS) {
  const p = path.join(WEB, d);
  if (fs.existsSync(p)) walk(p);
}
const blob = sources.map((f) => fs.readFileSync(f, "utf8")).join("\n");

// every dotted, quoted token that looks like a catalog key
const usedLiteral = new Set();
for (const m of blob.matchAll(/["'`]([a-zA-Z][\w-]*(?:\.[\w-]+)+)["'`]/g)) usedLiteral.add(m[1]);

const unused = [];
for (const key of enKeys) {
  if (usedLiteral.has(key)) continue;
  if (DYNAMIC_PREFIXES.some((p) => key.startsWith(p))) continue;
  unused.push(key);
}
if (unused.length) fail(`${unused.length} unused key(s): ${unused.join(", ")}`);

// ---- report ----
if (errors.length) {
  console.error(`✗ check-i18n FAILED (${errors.length} problem${errors.length > 1 ? "s" : ""}):`);
  for (const e of errors) console.error("  - " + e);
  process.exit(1);
}
console.log(`✓ check-i18n passed — ${enKeys.size} keys × ${LANGS.length} languages`);
console.log(
  `  parity · no empty values · placeholder parity · ` +
    `${REQUIRED_EXPLANATION_KEYS.length} explanation templates · no unused keys`,
);
