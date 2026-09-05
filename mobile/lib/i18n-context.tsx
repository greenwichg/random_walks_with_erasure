import * as React from "react";

import {
  formatCompact as coreFormatCompact,
  formatDate as coreFormatDate,
  localizeExplanation as coreLocalizeExplanation,
  normalizeLang,
  timeAgo as coreTimeAgo,
  type Lang,
  type TFunction,
} from "@ih/core/i18n/core";

import { deviceLang, translatorFor } from "./i18n.ts";
import { useSettings } from "./hooks.ts";

/**
 * `LanguageProvider` — the React half of localisation, as on the web (`web/lib/i18n.tsx`).
 *
 * The active language comes from the **Settings query**, the single source of truth: a save on the
 * settings screen writes the normalised settings into the React Query cache, so the whole tree
 * re-renders in the new language, live. What differs from the web is the fallback before settings
 * load (or when signed out): the web is English, a phone reads its own locale — the seam
 * `lib/i18n.ts` exists for.
 *
 * The resolver itself (`makeT`, interpolation, the language → English → key chain, the formatters)
 * is `@ih/core/i18n/core`, shared byte for byte. Nothing about what a string SAYS is decided here.
 */
export interface I18nValue {
  lang: Lang;
  t: TFunction;
  formatDate: (iso: string, opts: Intl.DateTimeFormatOptions) => string;
  formatCompact: (n: number) => string;
  timeAgo: (iso: string) => string;
  localizeExplanation: (exp: {
    type?: string;
    variant?: string;
    message?: string;
    evidence?: Record<string, unknown> | null;
  }) => string;
}

const I18nContext = React.createContext<I18nValue | null>(null);

function build(lang: Lang): I18nValue {
  const t = translatorFor(lang);
  return {
    lang,
    t,
    formatDate: (iso, opts) => coreFormatDate(iso, lang, opts),
    formatCompact: (n) => coreFormatCompact(n, lang),
    timeAgo: (iso) => coreTimeAgo(iso, lang, t),
    localizeExplanation: (exp) => coreLocalizeExplanation(exp, t),
  };
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const { data: settings } = useSettings();
  const lang = settings?.language ? normalizeLang(settings.language) : deviceLang();
  const value = React.useMemo(() => build(lang), [lang]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

/** The one hook screens use to translate + format in the active language. */
export function useTranslation(): I18nValue {
  const ctx = React.useContext(I18nContext);
  if (ctx) return ctx;
  // Outside the provider: the device language, so a stray render still reads as a sentence.
  return build(deviceLang());
}
