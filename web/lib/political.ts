import type { EmotionShare, Lean, LeanBucket } from "@/types/domain";
import { EMOTION_META } from "@/lib/metrics";

/** Map a continuous lean [-2, 2] to a coarse bucket (tau = 0.5, matches backend). */
export function leanBucket(lean: Lean, tau = 0.5): LeanBucket {
  if (lean < -tau) return "left";
  if (lean > tau) return "right";
  return "center";
}

/** A short human label for a lean value, e.g. "Lean left". */
export function leanLabel(lean: Lean): string {
  const bucket = leanBucket(lean);
  const strength = Math.abs(lean);
  if (bucket === "center") return "Center";
  const side = bucket === "left" ? "left" : "right";
  if (strength > 1.4) return `Strong ${side}`;
  if (strength > 0.9) return side.charAt(0).toUpperCase() + side.slice(1);
  return `Lean ${side}`;
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

export function emotionLabel(key: keyof EmotionShare): string {
  return EMOTION_META[key].label;
}
