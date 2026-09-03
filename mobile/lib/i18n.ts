import { getLocales } from "expo-localization";

import en from "@ih/core/i18n/messages/en.json";
import es from "@ih/core/i18n/messages/es.json";
import fr from "@ih/core/i18n/messages/fr.json";
import de from "@ih/core/i18n/messages/de.json";
import pt from "@ih/core/i18n/messages/pt.json";
import { DEFAULT_LANG, makeT, normalizeLang, type Catalog, type Lang, type TFunction } from "@ih/core/i18n/core";

/**
 * Translation on native — the platform half of the i18n split.
 *
 * `@ih/core/i18n/core` is the whole resolver: lookup, `{param}` interpolation, the
 * active-language → English → key fallback chain, and `localizeExplanation`, which turns a
 * recommendation's structured explanation into a sentence. None of it is duplicated here.
 *
 * What the platform has to answer is "which language", and it answers it differently: the web reads
 * `<html lang>` (`web/lib/active-lang.ts`), and a phone reads the device locale. That is precisely
 * the seam the Phase 2 split created, and this is the other side of it.
 *
 * The catalogs are the same five JSON files the web ships — 922 keys each, checked for parity by
 * `web/scripts/check-i18n.mjs`, which scans `packages/core` too. A card's explanation therefore
 * reads identically on both platforms because it is the same string resolved by the same function,
 * not because someone kept two copies in step.
 */

const CATALOGS: Record<Lang, Catalog> = {
  en: en as Catalog,
  es: es as Catalog,
  fr: fr as Catalog,
  de: de as Catalog,
  pt: pt as Catalog,
};

/** The device's language, clamped to one Hidden View ships. */
export function deviceLang(): Lang {
  const tag = getLocales()[0]?.languageCode ?? DEFAULT_LANG;
  return normalizeLang(tag);
}

/**
 * A translation function for a language.
 *
 * English is always the fallback catalog, so a key missing from a translation degrades to English
 * and then to the key itself — visible and greppable, never a blank space where a sentence was.
 */
export function translatorFor(lang: Lang): TFunction {
  return makeT(CATALOGS[lang], CATALOGS.en, lang);
}
