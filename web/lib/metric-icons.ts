import type { LucideIcon } from "lucide-react";
import { Brain, FileText, Gauge, HeartPulse, Layers, Newspaper, RadioTower, Scale } from "lucide-react";
import type { MetricKey } from "@ih/core/domain/types";

/**
 * The icon for each Information Health metric.
 *
 * Split out of the metric table so that table could move to @ih/core. It was the only thing in 187
 * lines of metric definitions that bound them to a platform: `lucide-react` renders SVG into the
 * DOM, which is meaningless on React Native, and mobile will supply its own icons for these same
 * eight keys.
 *
 * Keyed by `MetricKey` rather than merged back onto the meta object, so the compiler fails on a
 * ninth metric added to the shared table without an icon here — the failure this file exists to
 * force is a *missing* icon, and an optional field would swallow it.
 */
export const METRIC_ICONS: Record<MetricKey, LucideIcon> = {
  topicDiversity: Layers,
  sourceDiversity: Newspaper,
  reportingRatio: FileText,
  emotionalBalance: HeartPulse,
  echoChamber: RadioTower,
  viewpointBalance: Scale,
  openMindedness: Brain,
  confidence: Gauge,
};
