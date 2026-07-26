"use client";

import { activeLang } from "@/lib/i18n-core";
import { countryFlag, countryName, countryShortName } from "@/lib/countries";

/**
 * A country rendered for humans (Location Intelligence UX): decorative flag + the localized
 * display name, from nothing but the canonical ISO code the platform already carries.
 *
 *   <CountryBadge code="US" />   →   🇺🇸 United States   (narrow screens: 🇺🇸 USA)
 *
 * Accessibility: the flag is `aria-hidden` (decorative, per spec) and the name is real text, so
 * a screen reader announces exactly the country name. The responsive short form swaps via CSS
 * display, so only one name is ever exposed to the accessibility tree. Codes stay internal —
 * filtering/routing callers keep passing ISO codes; only what the reader sees changes.
 */
export function CountryBadge({ code, className }: { code: string; className?: string }) {
  const lang = activeLang();
  const flag = countryFlag(code);
  const full = countryName(code, lang);
  const short = countryShortName(code, lang);

  return (
    <span className={className}>
      {flag && (
        <span aria-hidden className="mr-1.5">
          {flag}
        </span>
      )}
      {short !== full ? (
        <>
          <span className="sm:hidden">{short}</span>
          <span className="hidden sm:inline">{full}</span>
        </>
      ) : (
        full
      )}
    </span>
  );
}
