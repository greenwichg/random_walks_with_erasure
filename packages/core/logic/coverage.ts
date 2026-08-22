import type { Coverage, MetricKey, ReportMode } from "../domain/types.ts";

/**
 * The single source of truth for the Estimate → Measured lifecycle across the app.
 *
 * Every surface (Dashboard, Health Report, Analytics, metric cards) reads its "are we an Estimate or a
 * Measured profile, and how far along?" state from here, so the terminology and the progress maths are
 * identical everywhere. This is presentation logic only — it never recomputes a score; it just reads
 * the backend's `mode` + `coverage`.
 */
export interface CoverageStatus {
  /** True while the reader is on an Initial Estimate (not yet a Measured profile). */
  isEstimate: boolean;
  /** Reads recorded toward the measured threshold (the honest count, in both modes). */
  reads: number;
  /** Reads required to unlock the Measured profile. */
  threshold: number;
  /** Reads still needed (0 once measured). */
  remaining: number;
  /** Progress toward the threshold, 0–100. */
  pct: number;
  /** Whether `reads` has reached `threshold` (⇔ Measured). */
  sufficient: boolean;
}

const DEFAULT_THRESHOLD = 5;

/**
 * Resolve the Estimate/Measured + progress state. `isEstimate` prefers the backend `mode`; when it is
 * absent it falls back to `coverage.sufficient` — a Measured report is always sufficient and an
 * Estimate never is, so the two signals agree. Missing coverage degrades to "0 of 5, still building".
 */
export function coverageStatus(mode?: ReportMode | null, coverage?: Coverage | null): CoverageStatus {
  const reads = Math.max(0, coverage?.reads ?? 0);
  const threshold = Math.max(1, coverage?.threshold ?? DEFAULT_THRESHOLD);
  const sufficient = coverage?.sufficient ?? reads >= threshold;
  const isEstimate = mode ? mode === "estimate" : !sufficient;
  const remaining = Math.max(0, threshold - reads);
  const pct = Math.min(100, Math.round((Math.min(reads, threshold) / threshold) * 100));
  return { isEstimate, reads, threshold, remaining, pct, sufficient };
}

/**
 * Which action unlocks each metric — a stable product fact (the behaviour a metric measures), NOT a
 * calculation. Used by the metric empty state so every unavailable insight explains what to do next.
 * Values are i18n catalog keys.
 */
export const METRIC_UNLOCK: Record<MetricKey, string> = {
  topicDiversity: "unlock.reads",
  sourceDiversity: "unlock.reads",
  reportingRatio: "unlock.reads",
  emotionalBalance: "unlock.reads",
  echoChamber: "unlock.political",
  viewpointBalance: "unlock.political",
  openMindedness: "unlock.reception",
  confidence: "unlock.reads",
};
