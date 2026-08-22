import { normalizeLang, DEFAULT_LANG, type Lang } from "@ih/core/i18n/core";

/**
 * The active language, on the web: `<html lang>`, which `LanguageProvider` keeps in sync.
 *
 * Split out of the i18n resolver so that resolver could move to @ih/core. It was the only line in
 * 147 lines of pure lookup and interpolation that touched the DOM — the module's own header had
 * described it as "the pure, dependency-free half of the localization system" for as long as it has
 * existed, and this function was the reason that was not quite true.
 *
 * "The active language" is genuinely a per-platform question, not an accident of implementation:
 * the web has an attribute a provider maintains, and a native app will read its own store. Every
 * other function in `@ih/core/i18n/core` takes `lang` as an argument, so this is the only place the
 * answer has to come from.
 *
 * For **module-level** formatters that cannot call the `useTranslation` hook (they are not React
 * components); component code should prefer the hook. Falls back to English on the server, where
 * there is no document to read.
 */
export function activeLang(): Lang {
  if (typeof document === "undefined") return DEFAULT_LANG;
  return normalizeLang(document.documentElement.lang);
}
