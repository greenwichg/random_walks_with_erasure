import type { FactualityKey } from "@ih/core/logic/factuality-distribution";

/**
 * Fixed colour + label assignment for the factuality vocabulary — the rater's own six levels,
 * best to worst, plus the `unrated` slice.
 *
 * Two rules this table encodes, both from `factuality-badge.tsx`:
 *   * The LABEL is the rater's own word, reused from the publisher profile's key space
 *     (`publishers.factuality.level.*`), so one translation serves both surfaces and neither can
 *     paraphrase the other's vocabulary. "Mostly Factual" is a mild reservation, "Mixed" a
 *     serious one — collapsing them tells a reader something the rater did not say.
 *   * `unrated` reuses the neutral --center rather than owning a hue, because it is not a level.
 *
 * Tokens live in globals.css and are spaced by perceived lightness, so the ramp reads as an order
 * rather than a set of hues; see the block there for the measured CVD separations.
 */
export const FACTUALITY_META: Record<FactualityKey, { token: string; labelKey: string }> = {
  very_high: { token: "fact-very-high", labelKey: "publishers.factuality.level.very_high" },
  high: { token: "fact-high", labelKey: "publishers.factuality.level.high" },
  mostly_factual: { token: "fact-mostly-factual", labelKey: "publishers.factuality.level.mostly_factual" },
  mixed: { token: "fact-mixed", labelKey: "publishers.factuality.level.mixed" },
  low: { token: "fact-low", labelKey: "publishers.factuality.level.low" },
  very_low: { token: "fact-very-low", labelKey: "publishers.factuality.level.very_low" },
  unrated: { token: "center", labelKey: "story.factuality.unrated" },
};

export const factualityColor = (key: FactualityKey) => `hsl(var(--${FACTUALITY_META[key].token}))`;
