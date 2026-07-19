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

/** Fields shared by every Information Health result — measured or estimated. */
export interface HealthReportBase {
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
  /** Real-reading coverage backing this result. */
  coverage: Coverage;
}

/** A measured Information Health Report — computed from a reader's real reads. Carries the
 *  article-level axis confidence and the full set of behavioral metrics. */
export interface MeasuredHealthReport extends HealthReportBase {
  mode: "measured";
  /** Per-reader confidence in the political axis (top-2 softmax margin mean). */
  axisConfidence: number;
}

/** An Initial Information Health Estimate — computed from selected outlets only. Metrics that
 *  cannot be honestly estimated from outlets are absent: there is no `axisConfidence`, and the
 *  behavioral Open-Mindedness metric does not appear in `metrics`. */
export interface EstimateHealthReport extends HealthReportBase {
  mode: "estimate";
}

/** The flagship Information Health result — an estimate or a measured report, discriminated by
 *  `mode`. Narrow on `report.mode` to reach measured-only fields like `axisConfidence`. */
export type HealthReport = MeasuredHealthReport | EstimateHealthReport;

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
    /** Today's total estimated reading minutes (per-read estimates, not measured dwell). */
    minutesRead?: number;
    politicalShare: number; // 0–1
    topTopics: string[];
    /** Today-vs-goal progress from the reader's stored daily goal; absent for anonymous/demo. */
    goalMinutes?: number;
    goalMet?: boolean;
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
  /** Article-level political classification — the flag behind the cross-cutting gate; omitted when unknown. */
  political?: boolean;
  imageUrl?: string;
  /** Short summary — populated for Discover/Stories (from the feed); omitted for recommendations. */
  description?: string;
  lean: Lean;
  leanBucket: LeanBucket;
  confidence: number; // 0–1
  emotion: EmotionShare;
  dominantEmotion: keyof EmotionShare;
  register: Register;
  publishedAt: string;
  readingMinutes: number;
  // Media + publisher logo (Commit 9; RSS/Atom media only). Absent → the card falls back to text-only.
  image?: string;
  imageWidth?: number;
  imageHeight?: number;
  imageMimeType?: string;
  imageSource?: string;
  imageAttribution?: string;
  publisherLogo?: string;
  publisherLogoDark?: string;
  publisherLogoSource?: string;
}

/** One semantic explanation part (Commit 23): a template discriminator + evidence-derived
 *  params — never a localized sentence. The UI localizes via the rec.reader.* /
 *  rec.contribution.* catalog templates (the Commit 20 pattern). */
export interface ExplanationPart {
  key: string;
  params?: Record<string, unknown>;
}

/** The Evidence Resolver's structured explanation (21a.3): UI shows `message`;
 *  tooling and the validation pipeline consume `type`/`priority`/`evidence`.
 *  Commit 23 adds the semantic `readerFact`/`contribution` parts — additive; `message`
 *  is unchanged and remains the validated whole + the `reason` mirror. */
export interface RecommendationExplanation {
  type: string;
  priority: number;
  variant?: string;
  readerFact?: ExplanationPart;
  contribution?: ExplanationPart;
  message: string;
  evidence?: Record<string, unknown>;
}

/** A recommendation = an article + the ONE evidence-backed explanation for it. */
export interface Recommendation {
  article: Article;
  /** Mirrors `explanation.message` (kept for back-compat with older payloads). */
  reason: string;
  /** Which extension produced it ("story" = the conditional Story-Match slot, RWE_STORY_SLOT). */
  strategy: "rwe-b" | "rwe-d" | "adaptive" | "story";
  /** Which metric it most helps. */
  helpsMetric: MetricKey;
  /** Whether it bridges the reader across the centre. */
  crossCutting: boolean;
  explanation?: RecommendationExplanation;
}

export type FeedbackAction = "save" | "ignore" | "read-later" | "like" | "dislike";

/* ------------------------------------------------------------------ *
 * Recommendation explainability (21a.2) — the payload behind the card's
 * "Why?" drawer, proxied from the engine's internal explain endpoint.
 * Every field is real recommender evidence; nothing here is templated.
 * ------------------------------------------------------------------ */

export interface ExplainStrategyEvidence {
  score: number;
  /** 1-based rank in this strategy's full candidate ranking; null = not ranked (seen). */
  rank: number | null;
  inSlice: boolean;
}

export interface RecommendationEvidence {
  articleId: string;
  url?: string;
  headline: string;
  publisher: string;
  lean: number;
  chosenBy: Recommendation["strategy"];
  rank: number;
  scorePercentile: number;
  match: "strong" | "good" | "candidate";
  byStrategy: Record<string, ExplainStrategyEvidence>;
  crossCutting: { value: boolean; userMeanLean: number; articleLean: number; gate: string };
  outletFamiliarity: { reads: number; share: number; band: "never" | "rarely" | "familiar" };
  /** |article lean − your weighted mean lean| — the "bridge distance". */
  leanGap: number;
  topicShare: { topic: string; share: number } | null;
  /** The report's own viewpoint computation with this article appended; null when the
   *  article isn't political or the reader is below the report's political minimum. */
  viewpointShift: {
    current: { left: number; center: number; right: number };
    after: { left: number; center: number; right: number };
    estimated: boolean;
    basis: string;
  } | null;
  longTail: { itemDegree: number; degreePercentile: number };
  connectivity: { readsWithinTwoHops: number; graphReads: number };
  topic?: string;
  publishedAt?: string | null;
  explanation?: RecommendationExplanation;
}

export interface RecommendationExplain {
  trace: {
    reader: {
      row: number;
      corpusRow: number;
      reads: { total: number; joined: number | null };
      meanLean: number;
    };
    graph: { users: number; items: number; edges: number };
    strategies: Record<
      string,
      { paramsUsed: Record<string, unknown>; candidates: number; seenExcluded: number }
    >;
    plan: { strategy: string; slice: number }[];
    dedupDropped: number;
    served: number;
  };
  recommendations: RecommendationEvidence[];
  notes: string[];
  /** Debugging identity of this exact recommendation instance (21a.2). */
  explainId?: string;
  corpusGeneration?: number;
  modelVersion?: { readingVersion: number; receptionVersion: number | string };
}

/**
 * A reader's cross-cutting recommendation reception — the behavioral signal behind
 * Open-Mindedness. `rate` = opened / shown over cross-cutting recs; `active` is whether enough
 * have been surfaced and opened for the metric to populate on the Measured report.
 */
export interface RecommendationReception {
  shownCross: number;
  openedCross: number;
  rate: number | null;
  threshold: number;
  active: boolean;
}

/** One reading-history entry. */
export interface HistoryEntry {
  id: string;
  article: Article;
  readAt: string;
  readingMinutes: number;
  completed: boolean;
  /** Additive attribution (Commit 14): where the read came from. Omitted for legacy/extension reads. */
  readSource?: string; // app | extension | <future import>
  openedFrom?: string; // recommendations | discover | stories | search | saved | ai-coach
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
  /** Nullable hero image contract — selected from the cluster's RSS media (Commit 9). */
  image?: string | null;
  imageWidth?: number | null;
  imageHeight?: number | null;
  imageMimeType?: string | null;
  imageSource?: string | null;
  imageAttribution?: string | null;
  topic: string;
  updatedAt: string;
  totalCoverage: number; // number of articles in the cluster
  /** Distinct publishers covering the event (from the FeedArticle-clustered Stories). */
  publisherCount?: number;
  publishers?: string[];
  publisherDiversity?: number;
  earliest?: string;
  latest?: string;
  firstPublished?: string;
  latestUpdate?: string;
  newest?: string;
  oldest?: string;
  timeSpanHours?: number;
  distribution: ViewpointDistribution;
  coverage: StoryCoverage[];
  timeline: { date: string; label: string }[];
  blindspotSide?: LeanBucket;
  /** Lightweight Story Intelligence badge attached by /api/stories (Commit 10) — no extra request. */
  freshness?: StoryFreshness;
  lifecycle?: StoryLifecycle;
}

// --- Story Intelligence (Commit 10) — deterministic, computed on top of a Story ----------------
/** Freshness band from the latest publication's age + recent velocity/burst. */
export type FreshnessBand = "Breaking" | "Developing" | "Active" | "Cooling" | "Archived";
/** Coarser lifecycle stage from age + momentum. */
export type StoryLifecycle = "Breaking" | "Developing" | "Mature" | "Archived";

export interface StoryFreshness {
  band: FreshnessBand;
  score: number; // 0–100
  latestAgeHours?: number | null;
  recentArticles?: number;
}

export interface StoryMomentum {
  state: "Growing" | "Stable" | "Declining";
  recentArticles: number;
  priorArticles: number;
  newPublishers: number;
}

export type StoryTimelineEventType =
  | "first_report"
  | "publisher_join"
  | "perspective_expansion"
  | "milestone"
  | "latest";

export interface StoryTimelineEvent {
  date: string;
  type: StoryTimelineEventType;
  label: string;
  publisher?: string;
  perspective?: LeanBucket;
  count?: number;
}

export interface StoryCoverageStatistics {
  publisherDiversity?: number | null;
  publisherCount?: number | null;
  articleCount: number;
  coverageDurationHours: number;
  coverageVelocityPerDay: number;
  coverageGrowth: { recent: number; prior: number; delta: number; ratio: number };
  politicalDistribution?: ViewpointDistribution | null;
  publisherDistribution: Record<string, number>;
}

export interface StoryNewSinceLastVisit {
  lastVisited: string | null;
  lastUpdated: string | null;
  count: number;
  articles: {
    publisher?: string;
    headline?: string;
    url?: string;
    leanBucket?: LeanBucket;
    publishedAt?: string;
  }[];
  publishers: string[];
  perspectives: LeanBucket[];
}

export type StoryAlertType =
  | "new_publisher"
  | "new_perspective"
  | "became_breaking"
  | "became_archived"
  | "coverage_doubled";

export interface StoryAlert {
  type: StoryAlertType;
  message: string;
  publishers?: string[];
  perspectives?: LeanBucket[];
}

export interface StoryIntelligence {
  storyId: string;
  freshness: StoryFreshness;
  lifecycle: StoryLifecycle;
  momentum: StoryMomentum;
  coverageStatistics: StoryCoverageStatistics;
  timeline: StoryTimelineEvent[];
  newSinceLastVisit: StoryNewSinceLastVisit;
  alerts: StoryAlert[];
  lastVisited: string | null;
  lastUpdated: string | null;
  diagnostics?: { computeMs: number; coverageCount: number; timelineEvents: number };
}

/** Story list request — every field optional; omitted params are unfiltered. */
export interface StoryQuery {
  topic?: string;
  publisher?: string;
  lean?: string;
  dateFrom?: string;
  dateTo?: string;
  sort?: "top" | "latest" | "oldest" | "publishers";
  limit?: number;
  offset?: number;
}

/** Paginated Story list — Discover and Stories both consume this from the single Story Service. */
export interface StoriesResponse {
  stories: Story[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
  remainingPages: number;
  sort: string;
}

/** Discover feed: the latest catalog articles plus the facet values for the filters. */
export interface DiscoverResponse {
  articles: Article[];
  topics: string[];
  publishers: string[];
}

/** Live catalog search request — every field optional; omitted params are unfiltered. */
export interface SearchParams {
  query?: string;
  publisher?: string;
  lean?: string;
  topic?: string;
  dateFrom?: string;
  dateTo?: string;
  source?: string;
  sort?: "newest" | "oldest" | "publisher" | "relevance";
  limit?: number;
  offset?: number;
}

/** Live catalog search response — paginated FeedArticle results (same Article shape as Discover). */
export interface SearchResponse {
  results: Article[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
  remainingPages: number;
  sort: string;
  queryMs?: number; // debug only
  ftsAvailable?: boolean; // debug only
}

/** AI Coach chat message. Every field beyond the v1 core is OPTIONAL: a v1 engine
 * (RWE_COACH_V2 off) never sends them, and the UI renders exactly as before. */
export interface CoachMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  /** Grounding: values the coach cited. v1 cites the report metrics; Coach v2 may cite any
   * engine evidence key ("served", "sourceShare.NPR", …) and names the surface it came from. */
  citations?: { metric: string; value: number | string; source?: string }[];
  /** Optional article suggestions attached to an answer. */
  suggestions?: Article[];
  // ---- Coach v2 (RWE_COACH_V2) — additive; absent on v1 replies ----
  /** Routed intent ("EXPLAIN.metric") + how it was resolved ("rule" | "llm" | "unresolved"). */
  intent?: string;
  resolution?: string;
  /** Suggested next questions, offered as tappable chips. */
  followUps?: string[];
  /** Full recommendation cards (same contract as /api/recommendations — reuse the card UI). */
  cards?: Recommendation[];
  /** Client-carried structured conversation state (binding-only, opaque): round-trip the most
   * recent one verbatim on the next send so "it" / "the first one" resolve server-side. */
  echo?: Record<string, unknown>;
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
  savedCount: number;          // the single "Saved" counter (Bookmarks merged into this)
}

/** The minimal shape needed to save an article: its id (the persistence key) plus whatever display
 *  fields the surface has (a full Article on Recs/Discover/Search; a partial one from Story coverage). */
export type SavableArticle = Pick<Article, "id"> & Partial<Article>;

/** One saved article as the engine returns it — the stored Article snapshot + when it was saved. */
export interface SavedArticle {
  articleId: string;
  article: SavableArticle;
  savedAt: string | null;
}

/** The result of a save/unsave — the resulting state plus the reader's live Saved total. */
export interface SaveResult {
  articleId: string;
  saved: boolean;
  savedCount: number;
}

export interface Settings {
  theme: "light" | "dark" | "system";
  language: string;
  politicalOpenness: number; // 0–100 → per-request RWE-B epsilon (50 = engine default 0.9)
  recommendationStrength: number; // 0–100 → per-request RWE-D beta (50 = engine default 0.5)
  readingGoalMinutes: number; // drives the dashboard's today-vs-goal progress
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

/** A per-user API token for the browser extension (metadata only — the plaintext is never stored). */
export interface ApiToken {
  id: number;
  label: string | null;
  createdAt: string | null;
  lastUsedAt: string | null;
}

/** The result of minting a token: the plaintext is present exactly once, at creation. */
export interface ApiTokenMint {
  id: number;
  token: string;
  label: string | null;
  createdAt: string | null;
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

/**
 * A materialised notification for the signed-in reader (from `GET /api/me/notifications`, newest
 * first). `kind` is mapped to icon / title / body / destination by `NotificationPresentation` (the
 * ONLY kind→UI layer); an unknown kind degrades gracefully. `payload` carries the kind's structured
 * fields (e.g. `count`, `overall`, `streakDays`, `reads`) used to interpolate the localized body.
 * `seenAt` is null until the reader opens/clicks it (the unread signal).
 */
export interface NotificationItem {
  id: number;
  kind: string;
  titleKey: string;
  payload: Record<string, unknown>;
  createdAt: string;
  seenAt: string | null;
  gatedBy: string;
}

/**
 * Article Analyzer (A2) — ANALYSIS CONTRACT v1, the anonymous analysis of one news URL. Mirrors
 * the engine's `article_analyzer.analyze` verbatim; the web treats the nulls as meaningful (an
 * unknown outlet is `lean: null`, never a guess). The reader-relative sections
 * (`recommendation` / `explanation` / `personal`) are typed but intentionally NOT rendered — they
 * arrive in A3/A4.
 */
export type AnalysisSource = "catalog" | "scored_url_only";

/** Provenance of the scored facts + the flat scoring projection. `register` / `confidence` are
 *  carried for contract completeness but deferred from the A2 UI (see analysis-presentation). */
export interface AnalysisScoring {
  outlet: string;
  lean: number | null;
  leanBucket: LeanBucket | null;
  topic: string;
  political: boolean;
  emotion: Partial<EmotionShare> | null;
  register: number | null;
  confidence: number | null;
}

/** A catalog-backed story membership: real distribution + honestly-derived missing viewpoints. */
export interface AnalysisStoryMember {
  matched: true;
  storyId: string;
  articleCount: number | null;
  publisherCount: number | null;
  distribution: ViewpointDistribution;
  missingViewpoints: LeanBucket[];
}

/** A non-catalog article: at most a "resembles a tracked story" advisory, never membership. */
export interface AnalysisStoryAdvisory {
  matched: false;
  similarStory: { storyId: string; similarity: number } | null;
}

export type AnalysisStory = AnalysisStoryMember | AnalysisStoryAdvisory;

/** A3.3 — the reader's "read next" pick. `source` says which system licensed the selection:
 *  "story" (a sibling of the analyzed article's own story cluster) or "feed" (the reader's real
 *  recommendation feed's top pick). `explanation` is the resolver's licensed sentence for the
 *  pick, rendered by the SAME reused recommendation renderer. */
export interface AnalysisNextArticle {
  source: string;
  article: Article;
  explanation: RecommendationExplanation | null;
}

/** A3 reader-relative verdict — the STABLE object the engine emits (never raw implementation
 *  signals). ``reasons`` is a closed vocabulary; ``blindSpotTopic`` is set when a "blind_spot"
 *  reason is present. Filled only for a signed-in measured reader; null otherwise. */
export interface AnalysisRecommendation {
  wouldBroaden: boolean;
  reasons: string[];
  blindSpotTopic: string | null;
  /** A3.3 (additive): the "read next" pick, or null when no honest pick exists. */
  nextArticle?: AnalysisNextArticle | null;
}

export interface AnalysisResult {
  analysisVersion: number;
  input: { url: string; canonicalUrl: string | null };
  status: "analyzed" | "invalid_url";
  source: AnalysisSource | null;
  article: Article | null;
  scoring: AnalysisScoring | null;
  story: AnalysisStory | null;
  // A3 — filled for a signed-in measured reader; null for anonymous / non-measured (the A2 shape).
  // The explanation reuses the recommendation-card renderer (presentRecommendation / localizeExplanation).
  recommendation: AnalysisRecommendation | null;
  explanation: RecommendationExplanation | null;
  personal: unknown | null;    // A4 — still unread/unrendered
  notes: string[];
}

/** The optional client-supplied page context that improves fetchless scoring (a subset of the
 *  read metadata the extension captures). All optional; never trusted for canonicalization. */
export interface AnalyzeMetadata {
  title?: string;
  description?: string;
  outlet?: string;
  category?: string;
}
