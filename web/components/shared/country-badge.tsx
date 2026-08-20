"use client";

import * as React from "react";
import { activeLang } from "@/lib/i18n-core";
import { countryFlagSrc, countryName, countryShortName } from "@/lib/countries";

/**
 * A country rendered for humans (Location Intelligence UX): decorative flag + the localized
 * display name, from nothing but the canonical ISO code the platform already carries.
 *
 *   <CountryBadge code="US" />   →   🇺🇸 United States   (narrow screens: 🇺🇸 USA)
 *
 * The flag is an IMAGE, not an emoji. A flag emoji is a pair of Unicode regional-indicator
 * letters the platform is expected to draw a flag for, and **Windows ships no flag glyphs at
 * all** — so every Windows browser rendered the two letters instead, and the same chip looked
 * different on desktop and phone. Nothing was broken in the code; there was no glyph to draw.
 * The artwork ships with the app (public/flags, MIT flag-icons) so every platform gets the same
 * chip, and the CSP already allows it (`img-src 'self'`) where it deliberately blocks CDN fonts.
 *
 * Accessibility: the flag is `alt=""` + `aria-hidden` (decorative, per spec) and the name is real
 * text, so a screen reader announces exactly the country name. The responsive short form swaps via
 * CSS display, so only one name is ever exposed to the accessibility tree. Codes stay internal —
 * filtering/routing callers keep passing ISO codes; only what the reader sees changes.
 */
export function CountryBadge({ code, className }: { code: string; className?: string }) {
  const lang = activeLang();
  const src = countryFlagSrc(code);
  const full = countryName(code, lang);
  const short = countryShortName(code, lang);
  // A code with no artwork (or a file that 404s) must degrade to the name alone — a broken-image
  // icon beside a country is worse than no flag.
  const [broken, setBroken] = React.useState(false);

  return (
    <span className={className}>
      {src && !broken && (
        // A 4:3 SVG rendered at a fixed 16x12: next/image adds a loader and layout machinery for
        // no benefit on a static SVG, and would need `dangerouslyAllowSVG` to serve one at all.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt=""
          aria-hidden
          width={16}
          height={12}
          loading="lazy"
          decoding="async"
          onError={() => setBroken(true)}
          className="mr-1.5 inline-block h-3 w-4 shrink-0 rounded-[2px] object-cover align-[-0.1em]"
        />
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
