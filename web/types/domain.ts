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

/**
 * Measurement metadata for a metric (ADR-001). Wraps a metric's value with how much of the reader's
 * reading it reflects (coverage = scope) and where the signal comes from (provenance), plus an
 * optional confidence (certainty). Coverage ≠ confidence: coverage says how many reads carried the
 * signal at all; confidence says how sure we are given the reads that did. `confidence` is omitted
 * unless a value genuinely represents prediction uncertainty (the current Emotion outputs do not).
 */
export interface Measurement {
  /** The dimension this measures, e.g. "viewpoint" | "emotion". */
  dimension: string;
  /** Scope: of the eligible reads, how many carried this dimension's signal. */
  coverage: {
    /** Eligible reads that carried the signal (the numerator). */
    observed: number;
    /** The honest denominator — reads the metric is about. */
    eligible: number;
    /** The eligibility population, e.g. "political_reads" | "all_reads". */
    basis: string;
  };
  /** Where the value comes from: `kind` = "authoritative" (looked up) | "derived" (inferred). */
  provenance: { kind: string; source: string };
  /** Certainty (optional) — absent unless it genuinely represents prediction uncertainty. */
  confidence?: number | null;
}

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
  /**
   * Whether the backend could measure this metric from the reader's activity. When `false`, the
   * card renders the "not enough data yet" empty state instead of a score — an EXPLICIT backend
   * signal, never inferred from `score === 0`. Absent on older payloads → treated as available.
   */
  available?: boolean;
  /** Why the metric is unavailable (e.g. "insufficient_data"); only set when `available` is false. */
  reason?: string | null;
  /** Reads that typically unlock the metric — informational, drives the empty-state hint. */
  minimumActivity?: number | null;
  /**
   * Measurement metadata (ADR-001): coverage + provenance for this metric, from the reader's scored
   * reads. Present only on the dimensions that carry one (Viewpoint / Emotion) of a measured report;
   * additive — absent on older payloads and other metrics. Meaningful even when `available` is false
   * (the coverage explains the empty state).
   */
  measurement?: Measurement;
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

/** One counted publisher fact: a label (topic / ISO country / ISO language) + article count. */
export interface LabelCount {
  label: string;
  count: number;
}

/** One under-covered topic: the catalog's counts/shares beside the publisher's — a counted
 *  comparison, never a score. */
export interface TopicGap {
  label: string;
  publisherCount: number;
  catalogCount: number;
  publisherShare: number;
  catalogShare: number;
}

/** Publisher Intelligence profile (GET /api/publishers/{name}) — curated registry facts +
 *  counted catalog facts. `rated: false` means the registry doesn't rate the outlet: lean is
 *  null/absent ("Not rated", never a fabricated Center — L2.2). Tone modules are omitted below
 *  their signal floor; counted lists are always present (possibly empty). */
export interface PublisherProfile {
  name: string;
  rated: boolean;
  lean?: Lean | null;
  leanBucket?: LeanBucket | null;
  /** Curated registry locality — absent when the outlet isn't in the registry. */
  registry?: { country?: string; region?: string; city?: string; scope?: string };
  /** Majority article host as a URL — the publisher's own site, counted not curated. */
  site?: string;
  articles: { total: number; firstSeen?: string; lastSeen?: string; perDay?: number };
  topics: LabelCount[];
  languages: LabelCount[];
  eventCountries: LabelCount[];
  /** Reporting/opinion/mixed counts over the n articles carrying a register signal. */
  registers?: { reporting: number; opinion: number; mixed: number; n: number };
  /** Mean emotion shares over the n articles carrying a real emotion vector. */
  emotion?: EmotionShare & { n: number };
  /** M2 — the catalog's biggest topics this publisher rarely touches: a counted comparison
   *  (publisher share under half the catalog's, or zero), omitted below its sample floors. */
  topicGaps?: TopicGap[];
  /** M2 — counted story co-membership (shared clustered events), omitted below its floor. */
  coCoverage?: { sharedStories: number; publishers: { publisher: string; stories: number }[] };
  recent: Article[];
  publisherLogo?: string;
  publisherLogoDark?: string;
  /** Where the logo came from: "registry" (curated), "wikimedia"/"wikipedia" (enriched), or
   *  "favicon" (the publisher's own domain asset). */
  publisherLogoSource?: string;
  /** Merged publisher facts. Curated registry values always win; Wikipedia/Wikidata only fills
   *  gaps, and `sources` records which of the two produced each field, so the page can attribute
   *  a founding year without implying the curated country came from the same place. Absent
   *  entirely when nothing is known — a publisher with no match still renders. */
  about?: PublisherAbout;
}

/** Provenance of one merged field. `counted` is measured from our own catalog (the host we
 *  actually observe them publishing from), not asserted by anyone. */
export type MetadataSource = "curated" | "counted" | "wikipedia" | "wikimedia";

/** Outcome of the last enrichment lookup. `ambiguous` means candidates existed but none could be
 *  confirmed as this outlet — recorded rather than guessed, and never rendered as fact. */
export type MetadataStatus = "ok" | "no_match" | "ambiguous" | "error";

export interface PublisherAbout {
  description?: string;
  founded?: string;
  headquarters?: string;
  country?: string;
  website?: string;
  parent?: string;
  wikipediaUrl?: string;
  sources?: Partial<Record<keyof Omit<PublisherAbout, "sources" | "status" | "refreshedAt" | "wikipediaUrl">, MetadataSource>>;
  status?: MetadataStatus;
  refreshedAt?: string;
}

export interface BlindSpot {
  topic: string;
  /** How under-consumed vs the catalog, 0–1 (bigger = larger gap). */
  gap: number;
  note: string;
}

/** One report field that fed an improvement's evidence — traceability for the bound numbers (RC2.1). */
export interface EvidenceBasis {
  field: string;
  label: string;
  value: number;
}

/** RC2.2 — a deterministic estimated impact range (percentile points) that replaces the old fixed +5.
 *  `method` is "simulated" (the reader's distribution perturbed by the action and re-percentiled) or
 *  "deficit" (a coarse guide from the score's distance below the typical reader). */
export interface ImpactEstimate {
  low: number;
  high: number;
  method: "simulated" | "deficit";
  metric: MetricKey;
  confidence: "high" | "medium" | "low";
  fromScore: number;
  toScore: { low: number; high: number };
  explanation: string;
}

/** RC2.3 — the signed-in reader's lifecycle state for one improvement recommendation. Present only on
 *  a signed-in report; absent for anonymous/demo and older payloads. */
export type ImprovementLifecycleState =
  | "generated"
  | "shown"
  | "viewed"
  | "accepted"
  | "in_progress"
  | "completed"
  | "dismissed"
  | "expired"
  | "superseded";

export interface ImprovementLifecycle {
  recKey: string;
  state: ImprovementLifecycleState;
  firstScore?: number;
  currentScore?: number;
  completedScore?: number;
  generatedAt?: string;
  shownAt?: string;
  viewedAt?: string;
  acceptedAt?: string;
  dismissedAt?: string;
  completedAt?: string;
  expiredAt?: string;
  supersededAt?: string;
  supersededBy?: string;
}

/** RC2.4 — why a recommendation was ordered or suppressed. `visible: false` means the feedback-aware
 *  ranker filtered it out (`reason` = completed | dismissed | overlaps:<family>). Nothing is hidden:
 *  `signals` lists every applied factor. */
export interface ImprovementRanking {
  rank: number | null;
  visible: boolean;
  priority: number;
  reason?: string | null;
  signals: { signal: string; effect: string }[];
}

export interface Improvement {
  id: string;
  title: string;
  detail: string;
  metric: MetricKey;
  /** Backward-compat scalar: the midpoint of `impactEstimate` when present. */
  impact: number;
  /** RC2.2 — dynamic estimated impact range; optional (absent on older payloads). */
  impactEstimate?: ImpactEstimate;
  /** RC2.3 — lifecycle state for the signed-in reader; optional. */
  lifecycle?: ImprovementLifecycle;
  /** RC2.4 — feedback-aware ranking/suppression for the signed-in reader; optional. */
  ranking?: ImprovementRanking;
  /** RC2.1 — user-specific evidence bound from data already in the report. All optional: absent on
   *  older payloads / when the report lacked grounded data, in which case `detail` stands alone. */
  /** The behaviour that surfaced this recommendation, e.g. "82% of your reading came from Reuters and BBC." */
  trigger?: string;
  /** The specific numbers behind it, e.g. "Reuters (47%) and BBC (35%) account for most of your reading." */
  evidence?: string;
  /** The concrete step, e.g. "Reading from an outlet beyond Reuters and BBC would widen your sources." */
  suggestedAction?: string;
  /** Which metric it improves, e.g. "Broadens your Source Diversity." */
  expectedBenefit?: string;
  /** Exactly which report fields populated the evidence (auditability). */
  evidenceBasis?: EvidenceBasis[];
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
  // Per-metric dimensional coverage (ADR-001) now lives on each metric's `measurement`
  // (Metric.measurement) — Viewpoint's coverage moved there and Emotion gained one.
}

/** An Initial Information Health Estimate — computed from selected outlets only. There is no
 *  `axisConfidence`. Metrics that cannot be honestly estimated from outlets (the behavioral
 *  Open-Mindedness and Confidence metrics) are still present in `metrics`, carrying
 *  `available: false` so the UI shows a consistent empty-state card rather than hiding them. */
export interface EstimateHealthReport extends HealthReportBase {
  mode: "estimate";
}

/** The flagship Information Health result — an estimate or a measured report, discriminated by
 *  `mode`. Narrow on `report.mode` to reach measured-only fields like `axisConfidence`. */
export type HealthReport = (MeasuredHealthReport | EstimateHealthReport) & {
  /** True when this report belongs to the EXHIBIT account and is being shown to somebody else.
   *
   *  The engine falls back to the exhibit's report for a signed-in reader with no reads and no
   *  onboarding. That report is genuinely `mode: "measured"` with `coverage.reads: 30` — it really
   *  is a measurement, of the wrong person — so nothing in the payload distinguished it, and a new
   *  beta tester saw "Measured · based on 30 reads" over a political distribution they had never
   *  produced. Anything that renders a measurement claim MUST check this first. */
  sample?: boolean;
};

/** A single trend point for the dashboard sparkline / analytics. */
export interface TrendPoint {
  date: string;
  overall: number;
  [metric: string]: number | string;
}

export interface DashboardSummary {
  overall: number;
  overallDelta: number;
  /** Estimate vs Measured for this reader, lifted verbatim from their report — so the Dashboard keeps
   *  the onboarding context instead of dropping it. Absent only on older payloads. */
  mode?: ReportMode;
  /** Reading coverage toward the measured threshold (`reads` is the honest progress in both modes). */
  coverage?: Coverage;
  /** See `HealthReport.sample` — the dashboard shows the same chip and would make the same claim. */
  sample?: boolean;
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
  /** House lean of the outlet — null/absent when the registry doesn't rate it (L2.2). */
  publisherLean?: Lean | null;
  topic: string;
  url?: string;
  /** Article-level political classification — the flag behind the cross-cutting gate; omitted when unknown. */
  political?: boolean;
  imageUrl?: string;
  /** Short summary — populated for Discover/Stories (from the feed); omitted for recommendations. */
  description?: string;
  /** Article political lean, and its bucket. Null/absent when the lean is unknown — an outlet the
   *  registry doesn't rate (L2.2): reading-history reads carry an explicit null; feed-catalog
   *  articles (Discover/Search/Stories coverage) omit the fields on the wire (`exclude_none`) —
   *  the GDELT long tail is mostly unrated. Only the recommendation path (corpus outlets, all
   *  rated) always fills both. Rendered as "Unknown", never Center, and excluded from lean
   *  aggregations. */
  lean?: Lean | null;
  leanBucket?: LeanBucket | null;
  /** Null/absent when the article is unenriched (L2.2 family): no fabricated 0.7 confidence,
   *  all-neutral emotion, or "reporting" register is ever serialised — the recommendation path
   *  fills its own values, the feed path omits what was never measured. */
  confidence?: number | null; // 0–1
  emotion?: EmotionShare | null;
  dominantEmotion?: keyof EmotionShare | null;
  register?: Register | null;
  publishedAt: string;
  readingMinutes: number;
  /** Canonical publisher-level location (Location Intelligence Phase 0): ISO 3166-1 alpha-2 /
   *  ISO 639-1. Omitted when the engine couldn't resolve one — never fabricated. */
  country?: string;
  language?: string;
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

/** The canonical (snake_case) recommendation-feedback signals the backend records. The card's
 *  `FeedbackAction` "read-later" maps to "read_later" at the service boundary; "save" is a separate
 *  concept (the Saved pipeline) and is never sent here. */
export type RecFeedbackType = "like" | "dislike" | "ignore" | "read_later";

/** One recorded feedback signal from `GET /api/me/recommendations/feedback`. */
export interface RecFeedbackEntry {
  articleId: string;
  feedback: RecFeedbackType;
  createdAt: string;
  updatedAt: string;
}

/** Ack from `POST /api/me/recommendations/feedback`. `changed` is false for an idempotent repeat. */
export interface RecFeedbackAck {
  ok: boolean;
  feedback: RecFeedbackType;
  changed: boolean;
}

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
  /** Null/absent for an unrated outlet (lean) or an unenriched article (register/emotion) —
   *  L2.2: the row shows nothing rather than a default. */
  lean?: Lean | null;
  leanBucket?: LeanBucket | null;
  register?: Register | null;
  emotion?: EmotionShare | null;
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
  /** ISO 3166-1 alpha-2: stories whose EVENT happened in this country (member consensus;
   *  publisher homes never substitute). Absent = "All", the whole feed. */
  country?: string;
  /** Coverage-gap lens: "any" = stories with a DETECTED gap (blindspotSide set); a side = that
   *  thin side exactly. Balanced-or-unknown stories never match. Absent = "All". */
  blindspot?: string;
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
  /** Story counts per EVENT country under the active topic/publisher/lean filters (computed
   *  server-side before the country filter) — the country picker's source of truth, so an
   *  offered country always returns ≥1 story. */
  countryFacets?: Record<string, number>;
  /** Story counts per DETECTED coverage-gap side, same faceting discipline — the gaps picker
   *  offers only sides returning ≥1 story; balanced-or-unknown stories are counted nowhere. */
  blindspotFacets?: Record<string, number>;
}

/** Discover feed: the latest catalog articles plus the facet values for the filters. */
export interface DiscoverResponse {
  articles: Article[];
  topics: string[];
  publishers: string[];
  /** country -> located-article count (event geography, non-provisional) — the country picker's
   *  option list; absent country = no content. Optional for older-engine tolerance. */
  countryFacets?: Record<string, number>;
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
  /** ISO 3166-1 alpha-2: articles about events IN this country (event geography only —
   *  the publisher's home is provenance, never a content filter). */
  country?: string;
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
  /** Structured Weekly Review (COMPARE.weekly_review replies only) — the dashboard-card form of
   * the same facts the prose cites; `content` remains the transcript/fallback rendering. */
  weeklyReview?: WeeklyReview;
  /** Client-carried structured conversation state (binding-only, opaque): round-trip the most
   * recent one verbatim on the next send so "it" / "the first one" resolve server-side. */
  echo?: Record<string, unknown>;
}

/** One score-trend series in the Weekly Review: raw metric key (localized client-side), the
 *  window's first/last overall values (null when unmeasured), and the snapshot count. */
export interface WeeklyTrend {
  metric: string;
  first: number | null;
  last: number | null;
  points: number | null;
}

export interface WeeklyReview {
  reads: number | null;
  outlets: number | null;
  topPublishers: { name: string; reads: number }[];
  trends: WeeklyTrend[];
  goalMinutes: number | null;
  storedGoals: string[] | null;
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

/** One country's catalog + registry facts (`GET /api/places/countries`). */
export interface CountryFacet {
  country: string;
  articles: number;
  publishers: number;
  registryPublishers: number;
}

/** Counted geographic facts about the reader's stored reads (`GET /api/me/geography`). */
export interface ReaderGeography {
  reads: number;
  located: number;
  countries: Record<string, number>;
  languages: Record<string, number>;
  scope: Record<string, number>;
}

/** A followed place (Location Intelligence Phase 1). */
export interface FollowedLocation {
  placeId: string;
  level: "country" | "region" | "city";
}

/** One notification category's per-channel switches (`Settings.notifications.categories`). */
export interface NotificationChannelPrefs {
  inApp: boolean;
  push: boolean;
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
    /**
     * Preferences by CATEGORY (what it is about) x CHANNEL (how it arrives) — the shape newer
     * notification kinds gate on, alongside the four per-kind booleans above (both are live). `push`
     * is part of the contract before there is a push channel to honour it, so enabling one later
     * needs no settings migration.
     */
    categories: {
      breaking: NotificationChannelPrefs;
      digests: NotificationChannelPrefs;
      recommendations: NotificationChannelPrefs;
      product: NotificationChannelPrefs;
    };
  };
  /** Location Intelligence Phase 1.5 — edition + followed places (Settings > Places). */
  edition?: string | null;
  locations?: FollowedLocation[];
  // NOTE: the engine's settings contract still carries a `privacy` group
  // (shareAnonymizedMetrics / personalizedAds); it is intentionally omitted from the frontend
  // type because nothing here reads it. A real backend response's extra `privacy` key is
  // harmlessly ignored (structural typing) and round-trips untouched on save — no data loss.
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
  /** Reading coverage toward the measured threshold — so Analytics carries the same Estimate-vs-Measured
   *  context (trends fill in as the reader builds their profile). */
  coverage?: Coverage;
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
 * unknown outlet is `lean: null`, never a guess). The reader-relative sections are filled for a
 * signed-in measured reader (`recommendation` / `explanation` in A3, `personal` in A4) and null
 * otherwise — the anonymous shape is exactly the A2 analysis.
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

/** The reader's three-tier outlet familiarity — the engine's closed band vocabulary. */
export type FamiliarityBand = "never" | "rarely" | "familiar";

/**
 * A4 — the reader's standing relative to the analyzed article: facts the engine read verbatim
 * from the measured context / stored report (set membership, counts, shares — never derived
 * projections). Every block nulls independently when its licensing data was absent.
 */
export interface AnalysisPersonal {
  /** The analyzed canonical URL is in the reader's history; `at` when the read time is known. */
  alreadyRead: { at: string | null } | null;
  /** The reader's familiarity band for the outlet — the same lookup behind the verdict reasons. */
  publisher: { name: string; band: FamiliarityBand } | null;
  /** The reader's measured share of the topic (null when their report claims none) + the stored
   *  report's blind-spot gap when the verdict flagged the topic. */
  topic: { topic: string; share: number | null; blindSpot: { gap: number | null } | null } | null;
  /** The article's lean bucket vs the reader's measured viewpoint shares; `addsMissing` only for
   *  a political article whose bucket share is exactly zero. */
  viewpoint: {
    articleBucket: LeanBucket;
    readerShares: ViewpointDistribution | null;
    addsMissing: boolean;
  } | null;
  /** The reader's own coverage of the article's story cluster (analyzed article excluded);
   *  present only when they have read at least one member. */
  story: { readCount: number; bucketsRead: LeanBucket[]; addsBucket: LeanBucket | null } | null;
}

export interface AnalysisResult {
  analysisVersion: number;
  input: { url: string; canonicalUrl: string | null };
  status: "analyzed" | "invalid_url";
  source: AnalysisSource | null;
  article: Article | null;
  scoring: AnalysisScoring | null;
  story: AnalysisStory | null;
  // A3/A4 — filled for a signed-in measured reader; null for anonymous / non-measured (the A2
  // shape). The explanation reuses the recommendation-card renderer (presentRecommendation /
  // localizeExplanation); `personal` is the reader-standing section.
  recommendation: AnalysisRecommendation | null;
  explanation: RecommendationExplanation | null;
  personal: AnalysisPersonal | null;
  // AI summary + bias analysis (docs/ARTICLE_INSIGHTS.md): generated asynchronously by the
  // engine, cached, attached when present. Optional for back-compat with pre-insights payloads.
  insights?: ArticleInsights | null;
  notes: string[];
}

/** AI-generated article insights. `bias` explains HOW the writing works — framing, tone, loaded
 *  language (quoted), omissions, and viewpoint — deliberately never a left/right label (the
 *  scored registry lean already covers placement). */
export interface ArticleInsights {
  summary: string;
  bias: {
    framing: string;
    tone: string;
    loadedLanguage: string[];
    omissions: string;
    viewpoint: string;
  } | null;
  model: string | null;
  generatedAt: string | null;
}

/** The optional client-supplied page context that improves fetchless scoring (a subset of the
 *  read metadata the extension captures). All optional; never trusted for canonicalization. */
export interface AnalyzeMetadata {
  title?: string;
  description?: string;
  outlet?: string;
  category?: string;
}
