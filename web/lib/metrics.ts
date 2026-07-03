import type { LucideIcon } from "lucide-react";
import {
  Layers,
  Newspaper,
  FileText,
  HeartPulse,
  RadioTower,
  Scale,
  Brain,
  Gauge,
} from "lucide-react";
import type { LeanBucket, MetricKey } from "@/types/domain";

export interface MetricMeta {
  key: MetricKey;
  label: string;
  short: string;
  /** One-line plain-language explanation (tooltip). */
  tooltip: string;
  /** Longer explanation for the report page. */
  description: string;
  icon: LucideIcon;
  /** Tailwind hue token used for accents/rings/charts. */
  hue: "primary" | "left" | "center" | "right" | "positive" | "caution";
  /** CSS color for charts (reads a theme variable). */
  color: string;
}

/**
 * The eight Information Health metrics. Ordering matches the report layout.
 * Descriptions are user-facing and deliberately jargon-free.
 */
export const METRICS: Record<MetricKey, MetricMeta> = {
  topicDiversity: {
    key: "topicDiversity",
    label: "Topic Diversity",
    short: "Topics",
    tooltip: "How many different subjects you read across.",
    description:
      "How evenly your reading spreads across subjects. A high score means a broad diet; a low score means you circle the same few topics.",
    icon: Layers,
    hue: "primary",
    color: "hsl(var(--primary))",
  },
  sourceDiversity: {
    key: "sourceDiversity",
    label: "Source Diversity",
    short: "Sources",
    tooltip: "How many distinct publishers you read.",
    description:
      "The effective number of publishers you rely on. Reading widely across outlets reduces the influence of any single newsroom's framing.",
    icon: Newspaper,
    hue: "left",
    color: "hsl(var(--left))",
  },
  reportingRatio: {
    key: "reportingRatio",
    label: "Reporting Ratio",
    short: "Reporting",
    tooltip: "How much of your reading is reporting vs. opinion.",
    description:
      "The share of your reading that is factual reporting rather than opinion or commentary. Balance matters — both have a place.",
    icon: FileText,
    hue: "positive",
    color: "hsl(var(--positive))",
  },
  emotionalBalance: {
    key: "emotionalBalance",
    label: "Emotional Balance",
    short: "Tone",
    tooltip: "How calm vs. charged the tone of your reading is.",
    description:
      "How much of your reading leans on fear and outrage versus calm analysis. A charged diet can distort perception over time.",
    icon: HeartPulse,
    hue: "caution",
    color: "hsl(var(--caution))",
  },
  echoChamber: {
    key: "echoChamber",
    label: "Echo Chamber Score",
    short: "Echo",
    tooltip: "How one-sided your political reading is.",
    description:
      "How balanced your left/right reading is. A higher score means you're less one-sided — you hear more than one side of contested topics.",
    icon: RadioTower,
    hue: "center",
    color: "hsl(var(--center))",
  },
  viewpointBalance: {
    key: "viewpointBalance",
    label: "Viewpoint Balance",
    short: "Viewpoint",
    tooltip: "How much you read across the centre.",
    description:
      "How much of your reading sits across the political centre from your own position — the reads that expose you to genuinely different views.",
    icon: Scale,
    hue: "right",
    color: "hsl(var(--right))",
  },
  openMindedness: {
    key: "openMindedness",
    label: "Open-Mindedness",
    short: "Openness",
    tooltip: "How often you click the other side when it's shown.",
    description:
      "When we show you opposing-viewpoint articles, how often you actually engage. It measures receptiveness, not just exposure.",
    icon: Brain,
    hue: "primary",
    color: "hsl(var(--primary))",
  },
  confidence: {
    key: "confidence",
    label: "Confidence",
    short: "Confidence",
    tooltip: "How reliable these estimates are for your reading.",
    description:
      "How confident the model is in the political-lean estimates behind your scores. Lower confidence means we weight ambiguous articles less.",
    icon: Gauge,
    hue: "center",
    color: "hsl(var(--center))",
  },
};

export const METRIC_ORDER: MetricKey[] = [
  "topicDiversity",
  "sourceDiversity",
  "reportingRatio",
  "emotionalBalance",
  "echoChamber",
  "viewpointBalance",
  "openMindedness",
  "confidence",
];

/** Metrics that appear on the report radar (confidence is contextual, not scored on it). */
export const RADAR_METRICS: MetricKey[] = [
  "topicDiversity",
  "sourceDiversity",
  "reportingRatio",
  "emotionalBalance",
  "echoChamber",
  "viewpointBalance",
];

type BandHue = "positive" | "caution" | "negative";

const BAND_HUE: Record<string, BandHue> = {
  Healthy: "positive",
  Fair: "caution",
  "Needs work": "negative",
  Unknown: "negative",
};

/**
 * Band label + hue for a 0–100 score — the *fallback* thresholds. The engine is the
 * source of truth; prefer `resolveBand(score, backendBand)` so the UI honours it. Hue
 * is sourced from `BAND_HUE` (one place) so the label→colour mapping isn't restated.
 */
export function scoreBand(score: number): { label: string; hue: BandHue } {
  const label = score >= 67 ? "Healthy" : score >= 40 ? "Fair" : "Needs work";
  return { label, hue: BAND_HUE[label] ?? "negative" };
}

/** Resolve a band, preferring the engine's value and falling back to local thresholds. */
export function resolveBand(score: number, band?: string | null): { label: string; hue: BandHue } {
  const hue = band ? BAND_HUE[band] : undefined;
  if (band && hue) return { label: band, hue };
  return scoreBand(score);
}

export const LEAN_META: Record<LeanBucket, { label: string; color: string; token: string }> = {
  left: { label: "Left", color: "hsl(var(--left))", token: "left" },
  center: { label: "Center", color: "hsl(var(--center))", token: "center" },
  right: { label: "Right", color: "hsl(var(--right))", token: "right" },
};

export const EMOTION_META: Record<
  keyof import("@/types/domain").EmotionShare,
  { label: string; color: string }
> = {
  fear: { label: "Fear", color: "hsl(var(--right))" },
  outrage: { label: "Outrage", color: "hsl(var(--caution))" },
  analysis: { label: "Analysis", color: "hsl(var(--left))" },
  positive: { label: "Positive", color: "hsl(var(--positive))" },
  neutral: { label: "Neutral", color: "hsl(var(--muted-foreground))" },
};
