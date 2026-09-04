#!/usr/bin/env node
/**
 * check-i18n — the build-gating i18n validator (Commit 20.1, Requirement 6).
 *
 * Fails (exit 1) if any of the following is wrong, so a broken catalog can never ship:
 *   1. Key parity      — all 5 catalogs (en/es/fr/de/pt) share an identical key set.
 *   2. No empty values — no key maps to "" / whitespace in any language.
 *   3. Placeholder parity — a key's interpolation arguments are identical across languages (so a
 *      translation can't silently drop a `{publisher}` a component depends on). "Arguments"
 *      includes what each ICU plural block selects on; see messageArgs().
 *   3b. No-placeholder keys — copy that must never interpolate a quantity (see NO_PLACEHOLDERS).
 *   3c. Well-formed plurals — balanced braces, and every plural block carries an `other` branch.
 *   4. Explanation coverage — every resolver (type, variant) from explanationKey() has a template.
 *   5. No unused keys  — every catalog key is referenced in source (literally, or under a
 *      documented dynamic-key prefix built via template literals).
 *   5b. No hand-rolled interpolation — no `t(key).replace("{x}", …)`, which cannot work once a key
 *      carries a plural block.
 *
 * Run: `node scripts/check-i18n.mjs` (wired into `npm run build` via `npm run check:i18n`).
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
// Plain ESM, not the .ts module next to it: this script runs on bare Node inside the Docker
// image build (Node 20, no type stripping), where a .ts import fails the build outright.
import { messageArgs } from "../../packages/core/i18n/message-format.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(__dirname, "..");
// The catalogs are shared data — @ih/core owns them, because mobile needs the same strings.
const CORE = path.resolve(WEB, "..", "packages", "core");
const MSG = path.join(CORE, "i18n", "messages");
const LANGS = ["en", "es", "fr", "de", "pt"];

/**
 * Dynamic key families constructed at runtime via template literals — a key under one of these
 * prefixes counts as "used" even though its full string never appears verbatim in source. Each is
 * annotated with where it is built so the allowlist stays auditable.
 */
const DYNAMIC_PREFIXES = [
  "explanation.",          // explanationKey() in packages/core/i18n/core.ts
  "emotion.",              // t(`emotion.${key}`) — badges, analytics, attention profile, history
  "filter.",              // t(`filter.${leanBucket}`) — lean labels in badges/why-drawer/stories
  "metric.",               // t(`metric.${key}.label|short|tooltip|description`)
  "band.",                 // t(`band.${label}`) — health band chip
  "rec.strategy.",         // t(`rec.strategy.${value}`) — recommendation filter tabs
  "storyIntel.lifecycle.", // t(`storyIntel.lifecycle.${lifecycle}`)
  "storyIntel.momentum.",  // t(`storyIntel.momentum.${state}`)
  "freshness.",            // t(`freshness.${band}`) — freshness badge
  "local.scope.",          // t(`local.scope.${scope}`) — publisher scope pill (Local News v1)
  "publishers.about.source.", // t(`publishers.about.source.${source}`) — per-field provenance label
  "report.period.",        // t(`report.period.${period}.title|subtitle|suffix`) — components/report/period-analytics.tsx
  "publishers.factuality.level.",  // t(`publishers.factuality.level.${value}`) — components/shared/factuality-badge.tsx
  "publishers.factuality.source.", // t(`publishers.factuality.source.${source}`) — same component
  "publishers.ownership.source.",  // t(`publishers.ownership.source.${type.source}`) — OwnershipCard in app/(app)/publishers/[name]/page.tsx
];

/**
 * Dynamic key families whose members are enumerated by a const tuple rather than a prefix — listed
 * individually so a typo in the tuple still trips the checker.
 */
DYNAMIC_PREFIXES.push(
  "publishers.about.founded",       // ABOUT_ROWS in app/(app)/publishers/[name]/page.tsx
  "publishers.about.headquarters",  // ditto — `parent` left the tuple for the Ownership card
);

/**
 * Required resolver explanation templates — mirrors the switch in explanationKey() (packages/core/i18n/core.ts).
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
//
// A value may carry ICU plural blocks (`{n, plural, one {# story} other {# stories}}`), so the
// arguments a key needs are its plain `{name}` placeholders PLUS the argument each plural block
// selects on. A language whose grammar does not change with the count keeps a plain `{n}` — German
// "Artikel" is the same word either way — and that must still count as the same argument, or the
// parity check would force noise into the catalog to satisfy itself.
const PLURAL_ARG = /\{\s*(\w+)\s*,\s*plural\s*,/g;
// The one parser, imported from the module that renders these messages at runtime — a second
// implementation here would be a second thing to keep right, and the first two hand-rolled ones
// both read `other {are}` as an argument named `are`.
const placeholders = (s) => messageArgs(String(s));

// ---- 3c. plural blocks must be well formed and must cover `other` ----
// `other` is the branch every language falls back to; a block without it renders its own source at
// the reader. Braces must balance for the same reason.
for (const lang of LANGS) {
  for (const [key, val] of Object.entries(cats[lang] ?? {})) {
    const str = String(val);
    if (!PLURAL_ARG.test(str)) continue;
    PLURAL_ARG.lastIndex = 0;
    let depth = 0;
    for (const ch of str) {
      if (ch === "{") depth++;
      else if (ch === "}") depth--;
      if (depth < 0) break;
    }
    if (depth !== 0) fail(`"${key}" (${lang}) has unbalanced braces in its plural block`);
    for (const m of str.matchAll(PLURAL_ARG)) {
      const body = str.slice(m.index + m[0].length);
      if (!/(^|[\s}])other\s*\{/.test(body))
        fail(`"${key}" (${lang}) has a plural block on {${m[1]}} with no \`other\` branch`);
    }
  }
}

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

// ---- 3b. keys that must never interpolate a quantity ----
// Placeholder PARITY (above) only makes the five languages agree with each other — it would happily
// pass a `{count}` added to all five at once. These keys are copy where a number was measured and
// found to be untrue, so the rule is absolute rather than relative: no interpolation, any language.
//
// notifications.recommendations_waiting.body carried `{count}` from the reader's unopened-rec
// tally, which counts cards SURFACED and not clicked rather than anything queued. Production showed
// a reader "3,023 recommendations are waiting for you" about a feed that is rebuilt and re-ranked on
// every request — there was no backlog of 3,023, or of any size. Restoring a placeholder here means
// restoring that claim, so the build stops it.
const NO_PLACEHOLDERS = ["notifications.recommendations_waiting.body"];
for (const key of NO_PLACEHOLDERS) {
  if (!enKeys.has(key)) {
    fail(`"${key}" is on the no-placeholder list but is missing from the catalogs`);
    continue;
  }
  for (const lang of LANGS) {
    const v = cats[lang]?.[key];
    if (v === undefined) continue; // already reported by parity
    const found = placeholders(v);
    if (found.size)
      fail(`"${key}" (${lang}) must not interpolate — found {${[...found].join("}, {")}}. This copy `
         + `states that recommendations exist; it must not claim how many.`);
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
//
// Both packages. The catalogs live here, but roughly a third of the keys are named by @ih/core —
// the notification table, the recommendation-explanation resolver, the analyse presentation — all
// of which moved there so the Expo app can share them. Scanning only web/ reported 64 perfectly
// live keys as unused, which is the failure mode this check exists to prevent, inverted.
const SRC_ROOTS = [WEB, path.resolve(WEB, "..", "packages", "core")];
const SRC_DIRS = ["app", "components", "lib", "hooks", "services", "types", "domain", "logic", "api", "i18n"];
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
for (const root of SRC_ROOTS) {
  for (const d of SRC_DIRS) {
    const p = path.join(root, d);
    if (fs.existsSync(p)) walk(p);
  }
}
const blob = sources.map((f) => fs.readFileSync(f, "utf8")).join("\n");

// ---- 5b. no hand-rolled interpolation on a translated string ----
// `t(key).replace("{x}", …)` predates plural support and stops working the moment a key gains a
// plural block: the reader is shown the message format instead of the message. It was how
// analyze.coverage.scope filled its two counts, and pluralising that key is what surfaced it.
// Params belong in the `t` call, which is the only place that knows the active language.
for (const file of sources) {
  const text = fs.readFileSync(file, "utf8");
  if (/\bt\((?:[^()]|\([^()]*\))*\)\s*(?:\/\/[^\n]*\n|\s)*\.replace\(\s*["'`]\{/.test(text)) {
    fail(
      `${path.relative(WEB, file)} interpolates a translated string by hand ` +
        `(t(...).replace("{…}")). Pass the values as t()'s params instead — a plural key cannot be ` +
        `patched after the fact.`,
    );
  }
}

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
const pluralKeys = Object.values(en).filter((v) => /,\s*plural\s*,/.test(v)).length;
console.log(
  `  parity · no empty values · placeholder parity · ${pluralKeys} plural messages · ` +
    `${REQUIRED_EXPLANATION_KEYS.length} explanation templates · no unused keys`,
);
