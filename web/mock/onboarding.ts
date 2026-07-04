import type { HealthReport, Outlet } from "@/types/domain";

/**
 * Dev-only fallbacks for the onboarding flow when the engine is offline. Real publisher
 * names + AllSides-style leans so the flow is exercisable without the backend; in production
 * the mock fallback is disabled and a real engine serves these.
 */
export const MOCK_OUTLETS: Outlet[] = [
  { id: "nyt", name: "The New York Times", lean: -0.6, leanBucket: "left", articles: 320 },
  { id: "wapo", name: "The Washington Post", lean: -0.5, leanBucket: "left", articles: 300 },
  { id: "cnn", name: "CNN", lean: -0.7, leanBucket: "left", articles: 280 },
  { id: "theatlantic", name: "The Atlantic", lean: -0.5, leanBucket: "left", articles: 150 },
  { id: "vox", name: "Vox", lean: -0.7, leanBucket: "left", articles: 130 },
  { id: "npr", name: "NPR", lean: -0.2, leanBucket: "center", articles: 210 },
  { id: "ap", name: "Associated Press", lean: 0.0, leanBucket: "center", articles: 400 },
  { id: "reuters", name: "Reuters", lean: 0.0, leanBucket: "center", articles: 380 },
  { id: "bbc", name: "BBC", lean: -0.1, leanBucket: "center", articles: 350 },
  { id: "bloomberg", name: "Bloomberg", lean: 0.1, leanBucket: "center", articles: 240 },
  { id: "wsj", name: "The Wall Street Journal", lean: 0.4, leanBucket: "center", articles: 260 },
  { id: "thedispatch", name: "The Dispatch", lean: 0.6, leanBucket: "right", articles: 90 },
  { id: "nationalreview", name: "National Review", lean: 0.8, leanBucket: "right", articles: 120 },
  { id: "foxnews", name: "Fox News", lean: 0.9, leanBucket: "right", articles: 300 },
];

/** A mock Initial Estimate (dev fallback) — always labeled mode:"estimate", zero-read coverage. */
export function mockEstimate(outlets: string[]): HealthReport {
  const n = Math.max(1, outlets.length);
  const overall = Math.min(88, 42 + n * 4);
  const band = overall >= 67 ? "Healthy" : overall >= 40 ? "Fair" : "Needs work";
  return {
    overall,
    overallDelta: 0,
    band,
    updatedAt: new Date().toISOString(),
    mode: "estimate",
    coverage: { reads: 0, threshold: 5, sufficient: false },
    metrics: [
      { key: "topicDiversity", score: 72, delta: 0, band: "Healthy", benchmark: 50 },
      { key: "sourceDiversity", score: Math.min(95, n * 13), delta: 0, band: "Fair", benchmark: 50 },
      { key: "viewpointBalance", score: 44, delta: 0, band: "Fair", benchmark: 50 },
      { key: "echoChamber", score: 55, delta: 0, band: "Fair", benchmark: 50 },
      { key: "emotionalBalance", score: 68, delta: 0, band: "Healthy", benchmark: 50 },
      { key: "reportingRatio", score: 50, delta: 0, band: "Fair", benchmark: 50 },
    ],
    viewpoint: { left: 0.34, center: 0.28, right: 0.38 },
    attention: { fear: 0.14, outrage: 0.12, analysis: 0.4, positive: 0.2, neutral: 0.14 },
    topics: [
      { topic: "Politics", share: 0.34, count: 0 },
      { topic: "Economy", share: 0.22, count: 0 },
      { topic: "World", share: 0.18, count: 0 },
    ],
    sources: [],
    blindSpots: [
      { topic: "Climate", gap: 0.6, note: "Climate is a big share of what's available, but light in the outlets you picked." },
    ],
    improvements: [
      {
        id: "imp_viewpointBalance",
        title: "Add two cross-cutting reads a week",
        detail: "Your selected outlets lean to one side. Two opposite-but-close reads a week lift Viewpoint Balance the most.",
        metric: "viewpointBalance",
        impact: 8,
      },
    ],
    axisConfidence: 0.5,
  };
}
