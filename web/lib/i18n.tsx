"use client";

/**
 * LanguageProvider (Commit 20) — the React half of the localization system.
 *
 * Sources the active language from the **existing Settings query** (the single source of truth):
 * `useUpdateSettings` already writes the saved settings into the React Query cache, so when Save
 * resolves this provider re-renders the whole tree in the new language — live, no refresh, no
 * cookie, no second store. Before settings load (anonymous / first paint) it is English.
 *
 * The pure lookup/format logic lives in i18n-core.ts (unit-tested); this file only wires it to
 * React + the settings cache + `document.documentElement.lang`.
 */
import * as React from "react";
import { useSettings } from "@/hooks/use-data";
import {
  makeT,
  normalizeLang,
  localizeExplanation as coreLocalizeExplanation,
  formatDate as coreFormatDate,
  formatCompact as coreFormatCompact,
  timeAgo as coreTimeAgo,
  type Lang,
  type TFunction,
} from "@/lib/i18n-core";

import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt.json";

const CATALOGS: Record<Lang, Record<string, string>> = { en, es, fr, de, pt };

interface I18nValue {
  lang: Lang;
  t: TFunction;
  /** Date in the active language. */
  formatDate: (iso: string, opts: Intl.DateTimeFormatOptions) => string;
  /** Compact number (1.2K) in the active language. */
  formatCompact: (n: number) => string;
  /** Relative time ("2h ago") with localized words. */
  timeAgo: (iso: string) => string;
  /** A resolver explanation → its localized sentence (from the structured `type`). */
  localizeExplanation: (exp: {
    type?: string;
    variant?: string;
    message?: string;
    evidence?: Record<string, unknown> | null;
  }) => string;
}

const I18nContext = React.createContext<I18nValue | null>(null);

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const { data: settings } = useSettings();
  const lang = normalizeLang(settings?.language);

  const value = React.useMemo<I18nValue>(() => {
    const t = makeT(CATALOGS[lang], CATALOGS.en, (key) => {
      if (process.env.NODE_ENV !== "production") console.warn(`[i18n] missing key: ${key}`);
    });
    return {
      lang,
      t,
      formatDate: (iso, opts) => coreFormatDate(iso, lang, opts),
      formatCompact: (n) => coreFormatCompact(n, lang),
      timeAgo: (iso) => coreTimeAgo(iso, lang, t),
      localizeExplanation: (exp) => coreLocalizeExplanation(exp, t),
    };
  }, [lang]);

  // Keep <html lang> in sync with the active language (a11y + correct hyphenation), replacing the
  // authority of the hardcoded attribute in the root layout. Live, on every switch.
  React.useEffect(() => {
    if (typeof document !== "undefined") document.documentElement.lang = lang;
  }, [lang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

/** The one hook screens use to translate + format in the active language. */
export function useTranslation(): I18nValue {
  const ctx = React.useContext(I18nContext);
  if (ctx === null) {
    // A safe English fallback if a component renders outside the provider (e.g. a unit render),
    // so a missing provider never crashes the tree — it just renders English.
    const t = makeT(CATALOGS.en, CATALOGS.en);
    return {
      lang: "en",
      t,
      formatDate: (iso, opts) => coreFormatDate(iso, "en", opts),
      formatCompact: (n) => coreFormatCompact(n, "en"),
      timeAgo: (iso) => coreTimeAgo(iso, "en", t),
      localizeExplanation: (exp) => coreLocalizeExplanation(exp, t),
    };
  }
  return ctx;
}
