/**
 * i18n core (Commit 20) — the pure, dependency-free half of the localization system.
 *
 * No React, no DOM, no imports: everything here is a pure function of its arguments, so it is trivially
 * unit-testable (`node --test packages/core/i18n/core.test.ts`) and shared by the React `LanguageProvider`
 * (web/lib/i18n.tsx) and, later, the Expo app. Business logic never lives in translations — catalogs are strings only; this
 * module just looks them up, interpolates `{params}`, and maps the recommendation resolver's
 * structured explanation `type` to a localized sentence.
 */

export const SUPPORTED = ["en", "es", "fr", "de", "pt"] as const;
export type Lang = (typeof SUPPORTED)[number];
export const DEFAULT_LANG: Lang = "en";

export type Catalog = Record<string, string>;

/** Clamp any stored value to a supported language, mirroring the engine's allowlist. */
export function normalizeLang(value: unknown): Lang {
  return (SUPPORTED as readonly string[]).includes(value as string) ? (value as Lang) : DEFAULT_LANG;
}

/*
 * `activeLang()` used to live here, reading `<html lang>`. It was the ONLY thing in this file that
 * touched a platform — the module header has always described it as "the pure, dependency-free half
 * of the localization system", and it was three lines away from being true.
 *
 * It moved to `web/lib/active-lang.ts`, because "the active language" is a question each platform
 * answers differently: the web reads the attribute its LanguageProvider keeps in sync, and a native
 * app will read its own store. Everything below takes `lang` as an argument and always did.
 */

/* ------------------------------------------------------------------------ plural selection */

/**
 * PLURALS — a minimal ICU `plural` subset, because English "1 stories" was showing in production
 * and the flat `{n}` substitution had no way to fix it.
 *
 * A catalog value may write:
 *
 *     "{stories, plural, one {# story} other {# stories}}"
 *
 * `#` renders the count. Branch labels are the CLDR categories (`zero one two few many other`) or
 * an exact `=N` match, which wins over the category. A missing category falls back to `other`.
 *
 * Why a real message format and not `key.one` / `key.other` suffixes: several strings count TWO
 * things at once — "{stories} stories across {publishers} publishers" is the one that prompted
 * this — and a per-key suffix can only agree with one of them. Selection has to happen per
 * argument, inside the string, which is exactly what ICU's syntax is for. Nothing else from ICU is
 * supported (no `select`, no nested plurals, no number skeletons); those would be a parser, and
 * this catalog does not need one.
 */

/** `Intl.PluralRules` is not free to construct, and `t` runs on every render. */
const RULES = new Map<string, Intl.PluralRules>();
function rulesFor(lang: string): Intl.PluralRules | null {
  const hit = RULES.get(lang);
  if (hit) return hit;
  try {
    const made = new Intl.PluralRules(lang);
    RULES.set(lang, made);
    return made;
  } catch {
    return null; // an environment without the locale data: everything reads as `other`
  }
}

/**
 * The count an argument carries, or NaN when it isn't one.
 *
 * A call site may pass a number (preferred — `t("storyCard.sources", { n: 4 })`) or a string a
 * caller already formatted. A formatted string only fails to parse once it has been compacted
 * ("1.2K"), and every count large enough to compact is `other` in all five supported languages,
 * which is where NaN lands anyway.
 */
function countOf(value: unknown): number {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() !== "") return Number(value);
  return Number.NaN;
}

const PLURAL_HEAD = /^\{\s*(\w+)\s*,\s*plural\s*,/;
const BRANCH_LABEL = /(=\d+|zero|one|two|few|many|other)\s*\{/g;

/** Index of the `}` closing a `{` that opened just before `from`, or -1 if the braces never balance. */
function closeBrace(str: string, from: number): number {
  let depth = 1;
  for (let j = from; j < str.length; j++) {
    if (str[j] === "{") depth++;
    else if (str[j] === "}" && --depth === 0) return j;
  }
  return -1;
}

/** Split a plural body into its `label {text}` branches, honouring braces inside a branch. */
function branchesOf(body: string): Map<string, string> {
  const out = new Map<string, string>();
  BRANCH_LABEL.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = BRANCH_LABEL.exec(body))) {
    const end = closeBrace(body, BRANCH_LABEL.lastIndex);
    if (end === -1) break; // unbalanced — stop rather than invent a branch
    out.set(m[1]!, body.slice(BRANCH_LABEL.lastIndex, end));
    BRANCH_LABEL.lastIndex = end + 1;
  }
  return out;
}

/**
 * The argument names a message needs: its plain `{name}` placeholders, plus the argument each
 * plural block selects on.
 *
 * Exported because the build gate (web/scripts/check-i18n.mjs) and the catalog unit test both
 * check that the five languages agree on this set, and a message format deserves ONE parser. The
 * two hand-rolled regexes that came before this both read `other {are}` as an argument named
 * `are`, which is what a branch body looks like to something that isn't actually parsing.
 */
export function messageArgs(template: string): Set<string> {
  const out = new Set<string>();
  const scan = (str: string) => {
    let plain = "";
    let i = 0;
    while (i < str.length) {
      const open = str.indexOf("{", i);
      if (open === -1) {
        plain += str.slice(i);
        break;
      }
      const head = PLURAL_HEAD.exec(str.slice(open));
      if (!head) {
        plain += str.slice(i, open + 1);
        i = open + 1;
        continue;
      }
      out.add(head[1]!);
      const bodyStart = open + head[0].length;
      const end = closeBrace(str, bodyStart);
      if (end === -1) break;
      for (const branch of branchesOf(str.slice(bodyStart, end)).values()) scan(branch);
      plain += str.slice(i, open);
      i = end + 1;
    }
    for (const m of plain.matchAll(/\{(\w+)\}/g)) out.add(m[1]!);
  };
  scan(template);
  return out;
}

/**
 * Expand every `{arg, plural, …}` block against `params`. Anything that is not a plural block —
 * including ordinary `{name}` placeholders and stray braces — is copied through untouched for the
 * substitution pass that follows.
 */
function expandPlurals(
  template: string,
  params: Record<string, unknown>,
  lang: string,
  formatNumber: (n: number) => string,
): string {
  if (!template.includes(", plural,") && !template.includes(",plural,")) return template;
  let out = "";
  let i = 0;
  while (i < template.length) {
    const open = template.indexOf("{", i);
    if (open === -1) {
      out += template.slice(i);
      break;
    }
    const head = PLURAL_HEAD.exec(template.slice(open));
    if (!head) {
      out += template.slice(i, open + 1);
      i = open + 1;
      continue;
    }
    const j = closeBrace(template, open + head[0].length);
    if (j === -1) {
      // Unbalanced braces: leave the rest verbatim so the damage is visible and greppable
      // rather than silently swallowed.
      out += template.slice(i);
      break;
    }
    const arg = head[1]!;
    const branches = branchesOf(template.slice(open + head[0].length, j));
    const n = countOf(params[arg]);
    const category = Number.isNaN(n) ? "other" : (rulesFor(lang)?.select(n) ?? "other");
    const chosen =
      branches.get(`=${n}`) ?? branches.get(category) ?? branches.get("other") ?? template.slice(open, j + 1);
    const shown = Number.isNaN(n) ? String(params[arg] ?? "") : formatNumber(n);
    out += template.slice(i, open) + chosen.replace(/#/g, shown);
    i = j + 1;
  }
  return out;
}

/**
 * Expand plural blocks, then replace `{name}` placeholders from `params` (missing params are left
 * as-is, never blanked). `lang` picks the plural rules; `formatNumber` renders `#`.
 */
export function interpolate(
  template: string,
  params?: Record<string, unknown>,
  opts?: { lang?: Lang; formatNumber?: (n: number) => string },
): string {
  if (!params) return template;
  const lang = opts?.lang ?? DEFAULT_LANG;
  const expanded = expandPlurals(template, params, lang, opts?.formatNumber ?? ((n) => formatCompact(n, lang)));
  return expanded.replace(/\{(\w+)\}/g, (m, k) =>
    params[k] === undefined || params[k] === null ? m : String(params[k]),
  );
}

export type TFunction = (key: string, params?: Record<string, unknown>) => string;

/**
 * Build a translation function with the fallback chain **active language → English → the key
 * itself** (so a missing key degrades to a visible, greppable key rather than a blank). `lang`
 * selects plural forms and formats the `#` inside them; `onMiss` lets the provider log misses in
 * development.
 *
 * `lang` is required rather than defaulted: a caller that forgot it would silently apply English
 * plural rules to another language, which is the exact class of bug this whole mechanism exists to
 * remove. There are five call sites and the compiler names them all.
 */
export function makeT(
  catalog: Catalog,
  fallback: Catalog,
  lang: Lang,
  onMiss?: (key: string) => void,
): TFunction {
  return (key, params) => {
    const hit = catalog[key] ?? fallback[key];
    if (hit === undefined) {
      onMiss?.(key);
      return key;
    }
    return interpolate(hit, params, { lang });
  };
}

/**
 * The message key for a recommendation explanation, derived from the resolver's structured
 * `type` (+ `variant`/`evidence` discriminators) rather than by translating the English prose
 * — Commit 21's structured contract is what makes this a lookup instead of MT of a sentence.
 */
export function explanationKey(exp: {
  type?: string;
  variant?: string;
  evidence?: Record<string, unknown> | null;
}): string {
  const ev = exp.evidence ?? {};
  switch (exp.type) {
    case "story_match":
      return `explanation.story_match.${exp.variant ?? "same_event"}`;
    case "topic_continuity":
      return ev.crossCutting
        ? "explanation.topic_continuity.perspective"
        : "explanation.topic_continuity.outlet";
    case "new_publisher":
      return ev.band === "never"
        ? "explanation.new_publisher.never"
        : "explanation.new_publisher.rarely";
    case "bridge":
      return "explanation.bridge";
    case "long_tail":
      return "explanation.long_tail";
    case "coverage_breadth":
      return ev.topic
        ? "explanation.coverage_breadth.topic"
        : "explanation.coverage_breadth.generic";
    default:
      return "";
  }
}

/**
 * Localize a resolver explanation to the active language. Interpolates the evidence the resolver
 * already computed (publisher names, topic) — so no fact is invented here — and falls back to the
 * server-provided English `message` if there is no template for the type (keeps the card honest).
 */
export function localizeExplanation(
  exp: { type?: string; variant?: string; message?: string; evidence?: Record<string, unknown> | null },
  t: TFunction,
): string {
  const key = explanationKey(exp);
  if (!key) return exp.message ?? "";
  const ev = exp.evidence ?? {};
  const params = {
    readPublisher: ev.readPublisher,
    recPublisher: ev.recPublisher,
    publisher: ev.publisher,
    topic: typeof ev.topic === "string" ? ev.topic.toLowerCase() : ev.topic,
  };
  const out = t(key, params);
  // if the key was missing (t returned the key verbatim), fall back to the server sentence
  return out === key ? exp.message ?? out : out;
}

/* ---------------------------------------------------------------- locale-aware formatting */

/** Date formatting in the active language (replaces the hardcoded `toLocaleDateString("en", …)`). */
export function formatDate(iso: string, lang: Lang, opts: Intl.DateTimeFormatOptions): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(lang, opts);
}

/** Compact number formatting in the active language (1.2K / 3.4M; suffixes are locale-sensitive). */
export function formatCompact(n: number, lang: Lang): string {
  return new Intl.NumberFormat(lang, { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

/**
 * Relative time ("2h ago") whose words come from the catalog (`time.*`) and whose date fallback is
 * locale-aware. `t` supplies the localized unit words; a caller passes the active `t`/`lang`.
 */
export function timeAgo(iso: string, lang: Lang, t: TFunction): string {
  // An unknown date renders as nothing — never "Invalid Date" and never a fabricated "just now".
  // Real articles whose publication time the catalog doesn't know arrive with publishedAt: "".
  if (!iso || Number.isNaN(new Date(iso).getTime())) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return t("time.justNow");
  if (mins < 60) return t("time.minutesAgo", { n: mins });
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return t("time.hoursAgo", { n: hrs });
  const days = Math.round(hrs / 24);
  if (days < 7) return t("time.daysAgo", { n: days });
  return formatDate(iso, lang, { month: "short", day: "numeric" });
}
