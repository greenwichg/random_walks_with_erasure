import type { EmotionShare, LeanBucket, OwnershipCategory } from "@ih/core/domain/types";
import type { FactualityKey } from "@ih/core/logic/factuality-distribution";

import type { Palette } from "@/design/tokens";

/**
 * Token → colour, for the vocabularies the web resolves through CSS variables.
 *
 * `LEAN_META.color` in `@ih/core/logic/metrics` is `hsl(var(--left))` — a string only a stylesheet
 * can read. `OWNERSHIP_META` and `FACTUALITY_META` on the web (`web/lib/*-meta.ts`) map a category
 * to a token NAME for the same reason. Native reads the palette directly, so this is the same three
 * tables with the palette as the lookup — the fixed assignments (a hue always means the same owner
 * type; `unknown`/`unrated` reuse the neutral centre) are unchanged.
 */
export type OwnershipKey = OwnershipCategory | "unknown";

export const OWNERSHIP_LABEL_KEY: Record<OwnershipKey, string> = {
  independent: "own.independent",
  individual: "own.individual",
  telecom: "own.telecom",
  government: "own.government",
  private_equity: "own.privateEquity",
  conglomerate: "own.conglomerate",
  corporation: "own.corporation",
  other: "own.other",
  unknown: "own.unknown",
};

const OWNERSHIP_TOKEN: Record<OwnershipKey, keyof Palette> = {
  independent: "ownIndependent",
  individual: "ownIndividual",
  telecom: "ownTelecom",
  government: "ownGovernment",
  private_equity: "ownPrivateEquity",
  conglomerate: "ownConglomerate",
  corporation: "ownCorporation",
  other: "ownOther",
  unknown: "center",
};

export const ownershipColor = (key: OwnershipKey, palette: Palette) => palette[OWNERSHIP_TOKEN[key]];

export const FACTUALITY_LABEL_KEY: Record<FactualityKey, string> = {
  very_high: "publishers.factuality.level.very_high",
  high: "publishers.factuality.level.high",
  mostly_factual: "publishers.factuality.level.mostly_factual",
  mixed: "publishers.factuality.level.mixed",
  low: "publishers.factuality.level.low",
  very_low: "publishers.factuality.level.very_low",
  unrated: "story.factuality.unrated",
};

const FACTUALITY_TOKEN: Record<FactualityKey, keyof Palette> = {
  very_high: "factVeryHigh",
  high: "factHigh",
  mostly_factual: "factMostlyFactual",
  mixed: "factMixed",
  low: "factLow",
  very_low: "factVeryLow",
  unrated: "center",
};

export const factualityColor = (key: FactualityKey, palette: Palette) => palette[FACTUALITY_TOKEN[key]];

/** `LEAN_META[bucket].color`, resolved. */
export const leanHex = (bucket: LeanBucket, palette: Palette) => palette[bucket];

/** `EMOTION_META[key].color`, resolved: fear → right, outrage → caution, analysis → left,
 *  positive → positive, neutral → muted foreground. */
export function emotionColor(key: keyof EmotionShare, palette: Palette): string {
  switch (key) {
    case "fear":
      return palette.right;
    case "outrage":
      return palette.caution;
    case "analysis":
      return palette.left;
    case "positive":
      return palette.positive;
    default:
      return palette.mutedForeground;
  }
}
