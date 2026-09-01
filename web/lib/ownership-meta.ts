import type { OwnershipCategory } from "@ih/core/domain/types";

export type OwnershipKey = OwnershipCategory | "unknown";

/**
 * Fixed color + label assignment for the ownership vocabulary — a hue always means the same
 * owner type, on every surface (the story panel's rings and bar, the publisher profile's strip).
 * Tokens live in globals.css and were CVD-validated pairwise in ring order, both themes;
 * `unknown` reuses the neutral center rather than owning a hue, because it is not an owner type.
 */
export const OWNERSHIP_META: Record<OwnershipKey, { token: string; labelKey: string }> = {
  independent: { token: "own-independent", labelKey: "own.independent" },
  individual: { token: "own-individual", labelKey: "own.individual" },
  telecom: { token: "own-telecom", labelKey: "own.telecom" },
  government: { token: "own-government", labelKey: "own.government" },
  private_equity: { token: "own-private-equity", labelKey: "own.privateEquity" },
  conglomerate: { token: "own-conglomerate", labelKey: "own.conglomerate" },
  corporation: { token: "own-corporation", labelKey: "own.corporation" },
  other: { token: "own-other", labelKey: "own.other" },
  unknown: { token: "center", labelKey: "own.unknown" },
};

export const ownershipColor = (key: OwnershipKey) => `hsl(var(--${OWNERSHIP_META[key].token}))`;
