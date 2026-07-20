import { getJson, postJson, deleteJson } from "@/services/api";
import type {
  AnalysisResult,
  AnalyzeMetadata,
  AnalyticsSeries,
  ApiToken,
  ApiTokenMint,
  CoachMessage,
  DashboardSummary,
  DiscoverResponse,
  FeedbackAction,
  HealthReport,
  HistoryEntry,
  NotificationItem,
  Profile,
  Recommendation,
  RecommendationExplain,
  RecommendationReception,
  RecFeedbackAck,
  RecFeedbackEntry,
  RecFeedbackType,
  SavableArticle,
  SavedArticle,
  SaveResult,
  SearchParams,
  SearchResponse,
  Settings,
  StoriesResponse,
  Story,
  StoryIntelligence,
  StoryQuery,
  TopicSlice,
} from "@/types/domain";

/**
 * Typed data-access layer. Every screen imports from here — never from axios
 * directly — so the transport is centralised and the return types are enforced.
 * Each function maps 1:1 to a backend endpoint (mock today, Python tomorrow).
 */
/** Card FeedbackAction → the backend's canonical feedback type. Only the four recorded signals are
 *  mapped; "save" is intentionally absent (the Saved pipeline handles it) so it records nothing. */
const FEEDBACK_WIRE: Partial<Record<FeedbackAction, RecFeedbackType>> = {
  like: "like",
  dislike: "dislike",
  ignore: "ignore",
  "read-later": "read_later",
};

export const services = {
  dashboard: () => getJson<DashboardSummary>("/dashboard"),
  // The honest union: a signed-in reader below the read threshold receives an ESTIMATE (no
  // axisConfidence), so callers must narrow on `mode` before touching measured-only fields.
  report: () => getJson<HealthReport>("/report"),
  recommendations: (strategy?: Recommendation["strategy"]) =>
    getJson<Recommendation[]>("/recommendations", strategy ? { strategy } : undefined),
  // 21a.2: the evidence behind the card's "Why?" drawer — fetched lazily on first open (D1).
  recommendationExplain: () => getJson<RecommendationExplain>("/recommendations/explain"),
  // Persist the reader's explicit feedback on a recommendation card. Maps the card's FeedbackAction
  // onto the backend's canonical type ("read-later" → "read_later") and posts to the authenticated
  // /api/me/recommendations/feedback route (real account state, no mock). "save" is a separate
  // concept (the Saved pipeline) and never reaches here, so it records nothing.
  sendFeedback: (articleId: string, action: FeedbackAction): Promise<RecFeedbackAck | null> => {
    const feedback = FEEDBACK_WIRE[action];
    if (!feedback) return Promise.resolve(null);
    return postJson<RecFeedbackAck>("/me/recommendations/feedback", { articleId, feedback });
  },
  // The reader's recorded feedback (oldest first) — used to keep an *ignored* card dismissed across
  // a page reload. Authenticated; anonymous readers have recorded nothing.
  recommendationFeedback: () => getJson<RecFeedbackEntry[]>("/me/recommendations/feedback"),
  // Record that the reader opened a recommended article — the Open-Mindedness reception signal.
  openRecommendation: (articleId: string, crossCutting: boolean) =>
    postJson<RecommendationReception>("/me/recommendations/opened", { articleId, crossCutting }),
  history: () => getJson<HistoryEntry[]>("/history"),
  topics: () => getJson<TopicSlice[]>("/topics"),
  // Discover: latest catalog articles + facets, with optional topic/publisher/lean filters + a size cap.
  discover: (filters?: { topic?: string; publisher?: string; lean?: string; limit?: number }) =>
    getJson<DiscoverResponse>("/discover", filters && Object.keys(filters).length ? filters : undefined),
  // Stories — the paginated Story envelope from the single Story Service (Discover consumes it too).
  stories: (query?: StoryQuery) => {
    const clean: Record<string, string> = {};
    if (query)
      for (const [k, v] of Object.entries(query)) {
        if (v !== undefined && v !== null && v !== "" && v !== "all") clean[k] = String(v);
      }
    return getJson<StoriesResponse>("/stories", Object.keys(clean).length ? clean : undefined);
  },
  story: (id: string) => getJson<Story>(`/stories/${id}`),
  // Deterministic Story Intelligence (freshness / lifecycle / momentum / timeline / new-since-last-visit).
  storyIntelligence: (id: string) => getJson<StoryIntelligence>(`/stories/${id}/intelligence`),
  profile: () => getJson<Profile>("/profile"),
  settings: () => getJson<Settings>("/settings"),
  // Persist a (partial) preferences patch; the engine merges + returns the full normalised settings.
  updateSettings: (patch: Partial<Settings>) => postJson<Settings>("/settings", patch),
  // Notifications (N3): the signed-in reader's materialised notifications, newest first.
  notifications: () => getJson<NotificationItem[]>("/me/notifications"),
  // Mark one notification seen (idempotent, user-scoped engine-side).
  markNotificationSeen: (id: number) =>
    postJson<{ ok: boolean; changed: boolean }>(`/me/notifications/${id}/seen`),
  analytics: () => getJson<AnalyticsSeries>("/analytics"),
  coachHistory: () => getJson<CoachMessage[]>("/coach"),
  // Coach v2: round-trips the last reply's structured echo verbatim (binding-only); omitted for
  // v1 transcripts so the request body stays exactly today's shape.
  coachSend: (message: string, echo?: Record<string, unknown>) =>
    postJson<CoachMessage>("/coach", echo ? { message, echo } : { message }),
  // Live catalog search — text + facet + date filters, sorting, and offset pagination.
  search: (params: SearchParams) => {
    const clean: Record<string, string> = {};
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "" && v !== "all") clean[k] = String(v);
    }
    return getJson<SearchResponse>("/search", Object.keys(clean).length ? clean : undefined);
  },
  // Per-user API tokens for the browser extension (auth'd; proxied to the engine server-side).
  apiTokens: () => getJson<ApiToken[]>("/me/tokens"),
  createApiToken: (label?: string) => postJson<ApiTokenMint>("/me/tokens", { label }),
  revokeApiToken: (id: number) => deleteJson<{ ok: boolean }>(`/me/tokens/${id}`),
  // Saved articles — the single "Saved" concept, persisted per user; drives the profile counter.
  saved: () => getJson<SavedArticle[]>("/me/saved"),
  saveArticle: (article: SavableArticle) =>
    postJson<SaveResult>("/me/saved", { articleId: article.id, article }),
  // article ids are URLs, so pass the id as an encoded query param (never a path segment).
  unsaveArticle: (articleId: string) =>
    deleteJson<SaveResult>(`/me/saved?articleId=${encodeURIComponent(articleId)}`),
  // Article Analyzer (A2): anonymous analysis of one URL (+ optional page metadata). ANALYSIS
  // CONTRACT v1, verbatim from the engine; read-only — nothing is stored.
  analyze: (url: string, metadata?: AnalyzeMetadata) =>
    postJson<AnalysisResult>("/analyze", metadata ? { url, metadata } : { url }),
};

/** React Query cache keys, colocated so invalidation stays consistent. */
export const queryKeys = {
  dashboard: ["dashboard"] as const,
  report: ["report"] as const,
  recommendations: (strategy?: string) => ["recommendations", strategy ?? "all"] as const,
  // under the ["recommendations"] prefix so slider saves invalidate it with the feed
  recommendationExplain: ["recommendations", "explain"] as const,
  history: ["history"] as const,
  topics: ["topics"] as const,
  discover: (filters?: { topic?: string; publisher?: string; lean?: string; limit?: number }) =>
    [
      "discover",
      filters?.topic ?? "all",
      filters?.publisher ?? "all",
      filters?.lean ?? "all",
      filters?.limit ?? "default",
    ] as const,
  stories: (query?: StoryQuery) =>
    [
      "stories",
      query?.topic ?? "all",
      query?.publisher ?? "all",
      query?.lean ?? "all",
      query?.sort ?? "top",
      query?.offset ?? 0,
    ] as const,
  story: (id: string) => ["story", id] as const,
  storyIntelligence: (id: string) => ["story-intelligence", id] as const,
  profile: ["profile"] as const,
  saved: ["saved"] as const,
  settings: ["settings"] as const,
  notifications: ["notifications"] as const,
  analytics: ["analytics"] as const,
  coach: ["coach"] as const,
  search: (params: SearchParams) =>
    [
      "search",
      params.query ?? "",
      params.publisher ?? "all",
      params.lean ?? "all",
      params.topic ?? "all",
      params.sort ?? "newest",
      params.offset ?? 0,
    ] as const,
  apiTokens: ["apiTokens"] as const,
  // Distinct top-level key (NOT under ["recommendations"]) so a slider-save invalidation of the feed
  // never churns the persisted-feedback cache the page reads to keep ignored cards dismissed.
  recommendationFeedback: ["recommendation-feedback"] as const,
};
