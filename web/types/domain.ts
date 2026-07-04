/**
 * Domain model — the single source of truth for every entity the product shows.
 *
 * These mirror the outputs of the Python backend services (RWE recommender,
 * Information Health Report, AI Coach, story clustering, etc.). The mock API
 * routes under app/api/* return exactly these shapes, so swapping in the real
 * backend is a base-URL change, not a type change.
 */

/** Political lean on a continuous [-2, 2] axis (backend's item/user positions). */
export type Lean = number;

/** Coarse political bucket derived from the continuous lean. */
export type LeanBucket = "left" | "center" | "right";

/** The eight health metrics + their identity. Keep in sync with the backend report. */
export type MetricKey =
  | "topicDiversity"
  | "sourceDiversity"
  | "reportingRatio"
  | "emotionalBalance"
  | "echoChamber"
  | "viewpointBalance"
  | "openMindedness"
  | "confidence";

/** Product health band for a 0–100 score. The backend is the source of truth for the
 * thresholds; the frontend `scoreBand()` is a fallback for data that lacks it. */
export type HealthBand = "Healthy" | "Fair" | "Needs work" | "Unknown";

/** A single scored metric (0–100) with the context the UI needs to render it. */
export interface Metric {
  key: MetricKey;
  /** 0–100 percentile-style score; higher is healthier. */
  score: number;
  /** Change vs the previous period, in points. */
  delta: number;
  /** Health band from the engine (source of truth). */
  band?: HealthBand;
  /** Raw underlying value + unit, for the "you read 12 topics" anchor. */
  raw?: { value: number; unit: string };
  /** Population median for the same metric (the "typical reader"). */
  benchmark?: number;
}

export interface EmotionShare {
  fear: number;
  outrage: number;
  analysis: number;
  positive: number;
  neutral: number;
}

export interface ViewpointDistribution {
  left: number;
  center: number;
  right: number;
}

export interface TopicSlice {
  topic: string;
  share: number; // 0–1
  count: number;
}

export interface SourceSlice {
  source: string;
  share: number; // 0–1
  count: number;
  lean: Lean;
}

/** A selectable publisher for onboarding (backend: GET /api/outlets). */
export interface Outlet {
  id: string;
  name: string;
  lean: Lean;
  leanBucket: LeanBucket;
  articles: number;
}

export interface BlindSpot {
  topic: string;
  /** How under-consumed vs the catalog, 0–1 (bigger = larger gap). */
  gap: number;
  note: string;
}

export interface Improvement {
  id: string;
  title: string;
  detail: string;
  metric: MetricKey;
  /** Expected points gained if followed. */
  impact: number;
}

/** Whether a report is a measured result (real reads) or an initial estimate (outlets only). */
export type ReportMode = "estimate" | "measured";

/** How much real reading backs a report — drives the Estimate→Measured transition. */
export interface Coverage {
  /** Real reads counted so far. */
  reads: number;
  /** Reads needed before an estimate becomes a measured report. */
  threshold: number;
  /** Whether `reads` has reached `threshold`. */
  sufficient: boolean;
}

/** The flagship Information Health Report (backend: health_report.py). Also the shape of the
 *  onboarding Initial Estimate, distinguished by `mode` (+ `coverage`). */
export interface HealthReport {
  overall: number;
  overallDelta: number;
  /** Health band for the overall score, from the engine (source of truth). */
  band?: HealthBand;
  updatedAt: string;
  metrics: Metric[];
  viewpoint: ViewpointDistribution;
  attention: EmotionShare;
  topics: TopicSlice[];
  sources: SourceSlice[];
  blindSpots: BlindSpot[];
  improvements: Improvement[];
  /** Per-reader confidence in the political axis (measured report). Omitted on an estimate. */
  axisConfidence: number;
  /** "measured" (real reads) or "estimate" (outlets-only onboarding result). */
  mode?: ReportMode;
  /** Real-reading coverage backing this report. */
  coverage?: Coverage;
}

/** A single trend point for the dashboard sparkline / analytics. */
export interface TrendPoint {
  date: string;
  overall: number;
  [metric: string]: number | string;
}

export interface DashboardSummary {
  overall: number;
  overallDelta: number;
  trend: TrendPoint[];
  today: {
    articlesRead: number;
    avgReadingMinutes: number;
    politicalShare: number; // 0–1
    topTopics: string[];
  };
  metrics: Metric[];
  streakDays: number;
}

/** Reporting-vs-opinion register + emotion of an article. */
export type Register = "reporting" | "opinion" | "mixed";

export interface Article {
  id: string;
  headline: string;
  publisher: string;
  publisherLean: Lean;
  topic: string;
  url?: string;
  imageUrl?: string;
  lean: Lean;
  leanBucket: LeanBucket;
  confidence: number; // 0–1
  emotion: EmotionShare;
  dominantEmotion: keyof EmotionShare;
  register: Register;
  publishedAt: string;
  readingMinutes: number;
}

/** A recommendation = an article + why RWE surfaced it + its health impact. */
export interface Recommendation {
  article: Article;
  reason: string;
  /** Which extension produced it. */
  strategy: "rwe-b" | "rwe-d" | "adaptive";
  /** Expected change to the overall health score if read. */
  healthImpact: number;
  /** Which metric it most helps. */
  helpsMetric: MetricKey;
  /** Whether it bridges the reader across the centre. */
  crossCutting: boolean;
}

export type FeedbackAction = "save" | "ignore" | "read-later" | "like" | "dislike";

/** One reading-history entry. */
export interface HistoryEntry {
  id: string;
  article: Article;
  readAt: string;
  readingMinutes: number;
  completed: boolean;
}

/** A clustered story: one event, many publishers across the spectrum. */
export interface StoryCoverage {
  publisher: string;
  headline: string;
  lean: Lean;
  leanBucket: LeanBucket;
  register: Register;
  emotion: EmotionShare;
  url?: string;
  publishedAt: string;
}

export interface Story {
  id: string;
  title: string;
  summary: string;
  topic: string;
  updatedAt: string;
  totalCoverage: number;
  distribution: ViewpointDistribution;
  coverage: StoryCoverage[];
  timeline: { date: string; label: string }[];
  blindspotSide?: LeanBucket;
}

/** AI Coach chat message. */
export interface CoachMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  /** Grounding: metric values the coach cited (so the UI can verify/link them). */
  citations?: { metric: MetricKey; value: number }[];
  /** Optional article suggestions attached to an answer. */
  suggestions?: Article[];
}

export interface Achievement {
  id: string;
  title: string;
  description: string;
  icon: string;
  unlocked: boolean;
  unlockedAt?: string;
  progress?: number; // 0–1 for locked ones
}

export interface Profile {
  name: string;
  handle: string;
  email: string;
  avatarUrl?: string;
  joinedAt: string;
  streakDays: number;
  longestStreak: number;
  scoreHistory: TrendPoint[];
  achievements: Achievement[];
  savedCount: number;
  bookmarkCount: number;
}

export interface Settings {
  theme: "light" | "dark" | "system";
  language: string;
  politicalOpenness: number; // 0–100 → maps to AdaptiveRWEB epsilon
  recommendationStrength: number; // 0–100 → RWE-B max_distance
  readingGoalMinutes: number;
  weeklyReport: boolean;
  monthlyReport: boolean;
  notifications: {
    recommendations: boolean;
    weeklyDigest: boolean;
    streakReminders: boolean;
    blindSpotAlerts: boolean;
  };
  privacy: {
    shareAnonymizedMetrics: boolean;
    personalizedAds: boolean;
  };
}

export interface AnalyticsSeries {
  readingOverTime: TrendPoint[];
  topicDiversity: TrendPoint[];
  politicalDiversity: TrendPoint[];
  publisherDiversity: TrendPoint[];
  emotion: { date: string; fear: number; outrage: number; analysis: number; positive: number; neutral: number }[];
  reporting: { date: string; reporting: number; opinion: number }[];
  recommendationAcceptance: { date: string; accepted: number; ignored: number }[];
  healthImprovement: TrendPoint[];
}
