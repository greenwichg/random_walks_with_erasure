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
 * drift. The whole palette is carried, including the ownership and factuality ramps: the story
 * breakdown draws them, and a ramp re-picked by eye would not survive the CVD checks the
 * stylesheet's values were measured against.
 */

export interface Palette {
  background: string;
  foreground: string;
  card: string;
  cardForeground: string;
  popover: string;
  popoverForeground: string;
  primary: string;
  primaryForeground: string;
  secondary: string;
  secondaryForeground: string;
  muted: string;
  mutedForeground: string;
  accent: string;
  accentForeground: string;
  destructive: string;
  destructiveForeground: string;
  border: string;
  input: string;
  ring: string;
  /** Political lean. Never re-used for anything else — see docs/SIGNAL_INTEGRITY.md. */
  left: string;
  center: string;
  right: string;
  positive: string;
  caution: string;
  negative: string;
  /** Ownership categorical palette — a hue always means the same owner type. */
  ownIndependent: string;
  ownIndividual: string;
  ownTelecom: string;
  ownGovernment: string;
  ownPrivateEquity: string;
  ownConglomerate: string;
  ownCorporation: string;
  ownOther: string;
  /** Factuality ORDINAL ramp, best to worst. */
  factVeryHigh: string;
  factHigh: string;
  factMostlyFactual: string;
  factMixed: string;
  factLow: string;
  factVeryLow: string;
}

/** `--background: 220 14% 98%` and friends, resolved. Light first, matching the stylesheet. */
export const light: Palette = {
  background: "#f9fafb",
  foreground: "#171a21",
  card: "#ffffff",
  cardForeground: "#171a21",
  popover: "#ffffff",
  popoverForeground: "#171a21",
  primary: "#543bce",
  primaryForeground: "#ffffff",
  secondary: "#f3f4f6",
  secondaryForeground: "#2b303b",
  muted: "#f3f4f6",
  mutedForeground: "#686f7d",
  accent: "#eeeff2",
  accentForeground: "#262b36",
  destructive: "#dc2828",
  destructiveForeground: "#ffffff",
  border: "#e2e4e9",
  input: "#dddfe4",
  ring: "#543bce",
  left: "#1b6eda",
  center: "#7b818e",
  right: "#db2430",
  positive: "#288f5f",
  caution: "#d57e0b",
  negative: "#db2430",
  ownIndependent: "#ae8629",
  ownIndividual: "#328577",
  ownTelecom: "#229bd3",
  ownGovernment: "#303791",
  ownPrivateEquity: "#904ebc",
  ownConglomerate: "#ae3251",
  ownCorporation: "#536d93",
  ownOther: "#cf5994",
  factVeryHigh: "#165f50",
  factHigh: "#298399",
  factMostlyFactual: "#60a0d7",
  factMixed: "#ed9b0c",
  factLow: "#cc4514",
  factVeryLow: "#951829",
};

export const dark: Palette = {
  background: "#131416",
  foreground: "#ebecef",
  card: "#1a1b1e",
  cardForeground: "#ebecef",
  popover: "#1c1e21",
  popoverForeground: "#ebecef",
  primary: "#8e7bea",
  primaryForeground: "#151619",
  secondary: "#26282b",
  secondaryForeground: "#dddfe4",
  muted: "#242529",
  mutedForeground: "#9196a1",
  accent: "#2b2d31",
  accentForeground: "#e8eaed",
  destructive: "#d34545",
  destructiveForeground: "#ffffff",
  border: "#2b2d31",
  input: "#35373b",
  ring: "#8e7bea",
  left: "#5199f0",
  center: "#9196a1",
  right: "#e85963",
  positive: "#40bf84",
  caution: "#f4a734",
  negative: "#e85963",
  ownIndependent: "#cfad59",
  ownIndividual: "#56b3a1",
  ownTelecom: "#78c1e2",
  ownGovernment: "#595ec0",
  ownPrivateEquity: "#ba8ed7",
  ownConglomerate: "#d07189",
  ownCorporation: "#8195b1",
  ownOther: "#d685ad",
  factVeryHigh: "#308272",
  factHigh: "#3ba1ba",
  factMostlyFactual: "#a5c5e9",
  factMixed: "#f4c871",
  factLow: "#e87a59",
  factVeryLow: "#d3223a",
};

/**
 * The type scale.
 *
 * Fewer steps than the web has, on purpose: a phone shows one column and roughly a third of the
 * text, so a scale with eight sizes on it produces distinctions nobody can see. These are the five
 * that carry meaning in a card. Components that reproduce a specific mobile-web size (a 26px lead
 * headline, an 11px kicker) set it directly through `Txt`; this scale is the default vocabulary.
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

/** `--radius: 0.6rem` (9.6px) is `md`; the web's `rounded-lg` (radius + 2px) is `lg`. */
export const radius = { xs: 2, sm: 6, md: 10, lg: 14, xl: 16, pill: 999 } as const;

/** The lean bucket a card is labelled with → its colour. Absent bucket ⇒ no colour, never Center. */
export function leanColor(bucket: string | null | undefined, palette: Palette): string | null {
  if (bucket === "left") return palette.left;
  if (bucket === "center") return palette.center;
  if (bucket === "right") return palette.right;
  // Unrated outlets exist and are common (L2.2). Rendering them as Center would be a fabricated
  // claim about a publisher's politics — the web renders "Unknown" and so does this.
  return null;
}

/**
 * `hsl(var(--x) / 0.12)` on native: a hex colour with an alpha channel appended.
 *
 * The web tints surfaces by putting an alpha on a token (`bg-primary/10`, `bg-lean-left/15`). RN
 * takes eight-digit hex, so the same tint is the token plus two hex digits — one helper, so the
 * arithmetic is written once.
 */
export function alpha(hex: string, a: number): string {
  const v = Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, "0");
  return `${hex}${v}`;
}
