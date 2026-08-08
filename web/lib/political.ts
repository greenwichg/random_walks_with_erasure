import type { EmotionShare, Lean, LeanBucket, ViewpointDistribution } from "@/types/domain";
import { EMOTION_META } from "./metrics.ts";

/**
 * The reader's PERSONAL coverage-gap lens, from their measured viewpoint distribution.
 *
 * Semantics (deliberate, and easy to invert by accident): `/stories?blindspot=left` lists stories
 * whose LEFT coverage is thin — stories left-leaning outlets underreport. Those are exactly the
 * stories a LEFT-heavy reading diet is most likely to have missed, so a reader who skews left gets
 * `"left"` — the lens named after their OWN side, not the side they read least.
 *
 * Honesty gates:
 *  - The caller must pass a MEASURED viewpoint (`report.mode === "measured"`) — the same rule the
 *    Political-balance tile already enforces; an estimate describes onboarding outlets, not reads.
 *  - A material skew is required (`minSkew`, default 15 points of share): near-balanced diets get
 *    `null` and no claim is made. Center is exposure, not a skew side, so it never names a lens.
 */
export function personalBlindspotSide(
  vp: ViewpointDistribution,
  minSkew = 0.15,
): Extract<LeanBucket, "left" | "right"> | null {
  const left = vp.left ?? 0;
  const right = vp.right ?? 0;
  if (left - right >= minSkew) return "left";
  if (right - left >= minSkew) return "right";
  return null;
}

/** Map a continuous lean [-2, 2] to a coarse bucket (tau = 0.5, matches backend). */
export function leanBucket(lean: Lean, tau = 0.5): LeanBucket {
  if (lean < -tau) return "left";
  if (lean > tau) return "right";
  return "center";
}

/**
 * The i18n message key for a lean value (e.g. `lean.leanLeft`). The label text itself lives in the
 * catalogs; components translate the key with `t()`. Keys are returned as string literals so the
 * i18n unused-key validator sees every one referenced.
 */
export function leanLabelKey(lean: Lean): string {
  const bucket = leanBucket(lean);
  // AllSides' own tiers on the registry lattice the backend serves (one UI value space since
  // docs/LEAN_CONSISTENCY.md F1): Lean Left/Right at ±1, Left/Right at ±2, cut at the lattice
  // midpoint 1.5. The former 0.9/1.4 cuts labelled AllSides "Lean" outlets as plain Left/Right
  // and invented a "Strong" tier AllSides does not have.
  const full = Math.abs(lean) >= 1.5;
  if (bucket === "center") return "lean.center";
  if (bucket === "left") return full ? "lean.left" : "lean.leanLeft";
  return full ? "lean.right" : "lean.leanRight";
}

/** Normalise a lean to a 0–100 position on the spectrum bar (0 = far left). */
export function leanToPercent(lean: Lean): number {
  return ((Math.max(-2, Math.min(2, lean)) + 2) / 4) * 100;
}

/** The dominant emotion of a share vector. */
export function dominantEmotion(e: EmotionShare): keyof EmotionShare {
  return (Object.keys(e) as (keyof EmotionShare)[]).reduce((a, b) => (e[a] >= e[b] ? a : b));
}

export function emotionColor(key: keyof EmotionShare): string {
  return EMOTION_META[key].color;
}
