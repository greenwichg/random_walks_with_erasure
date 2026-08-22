/**
 * Hidden View's palette, on native.
 *
 * Every value is transcribed from `web/app/globals.css` rather than picked to look similar. The
 * lean colours in particular are not decoration: `--left`, `--center` and `--right` are what a
 * reader learns to read a coverage plate by, and a native app that shifted them by a few degrees
 * would be quietly telling a different story about the same article.
 *
 * The web keeps these as HSL triples in CSS custom properties so a media query can swap the whole
 * set. React Native has no cascade, so they are resolved to hex here and selected by `useTheme()`.
 * The source of truth stays the stylesheet — `design/tokens.test.ts` reads it and fails if these
 * drift.
 */

export interface Palette {
  background: string;
  foreground: string;
  card: string;
  primary: string;
  primaryForeground: string;
  muted: string;
  mutedForeground: string;
  border: string;
  /** Political lean. Never re-used for anything else — see docs/SIGNAL_INTEGRITY.md. */
  left: string;
  center: string;
  right: string;
  positive: string;
  caution: string;
}

/** `--background: 220 14% 98%` and friends, resolved. Light first, matching the stylesheet. */
export const light: Palette = {
  background: "#f9fafb",
  foreground: "#171a21",
  card: "#ffffff",
  primary: "#543bce",
  primaryForeground: "#ffffff",
  muted: "#f3f4f6",
  mutedForeground: "#686f7d",
  border: "#e2e4e9",
  left: "#1b6eda",
  center: "#7b818e",
  right: "#db2430",
  positive: "#288f5f",
  caution: "#d57e0b",
};

export const dark: Palette = {
  background: "#131416",
  foreground: "#ebecef",
  card: "#1a1b1e",
  primary: "#8e7bea",
  primaryForeground: "#151619",
  muted: "#242529",
  mutedForeground: "#9196a1",
  border: "#2b2d31",
  left: "#5199f0",
  center: "#9196a1",
  right: "#e85963",
  positive: "#40bf84",
  caution: "#f4a734",
};

/**
 * The type scale.
 *
 * Fewer steps than the web has, on purpose: a phone shows one column and roughly a third of the
 * text, so a scale with eight sizes on it produces distinctions nobody can see. These are the five
 * that carry meaning in a card.
 */
export const type = {
  display: { fontSize: 28, lineHeight: 34, fontWeight: "700" as const },
  title: { fontSize: 20, lineHeight: 26, fontWeight: "600" as const },
  headline: { fontSize: 16, lineHeight: 22, fontWeight: "600" as const },
  body: { fontSize: 15, lineHeight: 21, fontWeight: "400" as const },
  caption: { fontSize: 13, lineHeight: 18, fontWeight: "400" as const },
  label: { fontSize: 11, lineHeight: 14, fontWeight: "600" as const, letterSpacing: 0.4 },
};

/** A 4pt grid. Named rather than numeric so a screen cannot invent a 7. */
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

export const radius = { sm: 6, md: 10, lg: 14, pill: 999 } as const;

/** The lean bucket a card is labelled with → its colour. Absent bucket ⇒ no colour, never Center. */
export function leanColor(bucket: string | null | undefined, palette: Palette): string | null {
  if (bucket === "left") return palette.left;
  if (bucket === "center") return palette.center;
  if (bucket === "right") return palette.right;
  // Unrated outlets exist and are common (L2.2). Rendering them as Center would be a fabricated
  // claim about a publisher's politics — the web renders "Unknown" and so does this.
  return null;
}
