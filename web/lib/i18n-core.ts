/**
 * i18n core (Commit 20) — the pure, dependency-free half of the localization system.
 *
 * No React, no imports: everything here is a pure function of its arguments, so it is trivially
 * unit-testable (`node --test web/lib/i18n-core.test.ts`) and shared by the React `LanguageProvider`
 * (web/lib/i18n.tsx). Business logic never lives in translations — catalogs are strings only; this
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

/**
 * The active language read from `<html lang>` — the authoritative value the LanguageProvider keeps
 * in sync. For **module-level** formatters that can't call the `useTranslation` hook (they aren't
 * React components); component code should prefer the hook. Falls back to English on the server.
 */
export function activeLang(): Lang {
  if (typeof document === "undefined") return DEFAULT_LANG;
  return normalizeLang(document.documentElement.lang);
}

/** Replace `{name}` placeholders from `params` (missing params are left as-is, never blanked). */
export function interpolate(template: string, params?: Record<string, unknown>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (m, k) =>
    params[k] === undefined || params[k] === null ? m : String(params[k]),
  );
}

export type TFunction = (key: string, params?: Record<string, unknown>) => string;

/**
 * Build a translation function with the fallback chain **active language → English → the key
 * itself** (so a missing key degrades to a visible, greppable key rather than a blank). `onMiss`
 * lets the provider log misses in development.
 */
export function makeT(catalog: Catalog, fallback: Catalog, onMiss?: (key: string) => void): TFunction {
  return (key, params) => {
    const hit = catalog[key] ?? fallback[key];
    if (hit === undefined) {
      onMiss?.(key);
      return key;
    }
    return interpolate(hit, params);
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
