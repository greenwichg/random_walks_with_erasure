import { getJson, postJson, deleteJson } from "./client.ts";
import { requestParams } from "../logic/request-params.ts";
import { discoverKey, type DiscoverFilters } from "../logic/discover-params.ts";
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
  CountryFacet,
  NotificationItem,
  ReaderGeography,
  Profile,
  PublisherProfile,
  Recommendation,
  RecommendationExplain,
  RecommendationReception,
  RecFeedbackAck,
  RecFeedbackEntry,
  RecFeedbackRemoveAck,
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
} from "../domain/types.ts";

/**
 * Typed data-access layer. Every screen imports from here — never from axios
 * directly — so the transport is centralised and the return types are enforced.
 * Each function maps 1:1 to a backend endpoint (mock today, Python tomorrow).
 */
/** Card FeedbackAction → the backend's canonical feedback type. Only recorded signals are mapped;
 *  "save" is intentionally absent (the Saved pipeline handles it) so it records nothing. */
const FEEDBACK_WIRE: Partial<Record<FeedbackAction, RecFeedbackType>> = {
  like: "like",
  dislike: "dislike",
  ignore: "ignore",
  "read-later": "read_later",
  "another-viewpoint": "another_viewpoint",
  "already-know": "already_know",
  "too-repetitive": "too_repetitive",
  "fewer-from-source": "fewer_from_source",
  "more-topic": "more_topic",
};

/** A card action's canonical wire type — exported for the undo path (removal names the type). */
export function feedbackWire(action: FeedbackAction): RecFeedbackType | undefined {
  return FEEDBACK_WIRE[action];
}

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
  // a page reload and to render Settings' "active feedback effects" list. Authenticated; anonymous
  // readers have recorded nothing.
  recommendationFeedback: () => getJson<RecFeedbackEntry[]>("/me/recommendations/feedback"),
  // The undo behind the visible-consequence UI: remove one recorded signal (or, with `feedback`
  // omitted, every signal the reader gave the article). A consequence the reader can see but not
  // retract would be surveillance, so removal is as first-class as recording.
  removeFeedback: (articleId: string, feedback?: RecFeedbackType) =>
    deleteJson<RecFeedbackRemoveAck>("/me/recommendations/feedback",
      feedback ? { articleId, feedback } : { articleId }),
  // Record that the reader opened a recommended article — the Open-Mindedness reception signal.
  openRecommendation: (articleId: string, crossCutting: boolean) =>
    postJson<RecommendationReception>("/me/recommendations/opened", { articleId, crossCutting }),
  history: () => getJson<HistoryEntry[]>("/history"),
  topics: () => getJson<TopicSlice[]>("/topics"),
  // Discover: latest catalog articles + facets, with optional topic/publisher/lean/country filters
  // + a size cap. `country` is EVENT geography (ISO alpha-2), the same rule as Stories.
  discover: (filters?: DiscoverFilters) =>
    getJson<DiscoverResponse>("/discover", filters && Object.keys(filters).length ? filters : undefined),
  // Stories — the paginated Story envelope from the single Story Service (Discover consumes it too).
  stories: (query?: StoryQuery) => {
    const clean = requestParams(query ?? {});
    return getJson<StoriesResponse>("/stories", Object.keys(clean).length ? clean : undefined);
  },
  story: (id: string) => getJson<Story>(`/stories/${id}`),
  publisher: (name: string) => getJson<PublisherProfile>(`/publishers/${encodeURIComponent(name)}`),
  // Deterministic Story Intelligence (freshness / lifecycle / momentum / timeline / new-since-last-visit).
  storyIntelligence: (id: string) => getJson<StoryIntelligence>(`/stories/${id}/intelligence`),
  profile: () => getJson<Profile>("/profile"),
  settings: () => getJson<Settings>("/settings"),
  // Deployment-level push availability (R1b: cached via react-query so the reconciler, the settings
  // toggle and the account-level preference share ONE fetch instead of each asking again).
  pushConfig: () => getJson<{ enabled: boolean; publicKey: string }>("/push/config"),
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
    const clean = requestParams(params);
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
  // Location Intelligence: located-catalog ∪ registry facts per country (feeds the Stories
  // country filter + Search + Settings places). The registry-publishers endpoint
  // (/api/places/publishers) remains an engine platform surface but currently has no web
  // consumer — the future personalized Local experience reintroduces it.
  placeCountries: () => getJson<CountryFacet[]>("/places/countries"),
  // Geographic Diversity readiness: the reader's counted geography (auth'd).
  geography: () => getJson<ReaderGeography>("/me/geography"),
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
  // Identity lives in lib/discover-params.ts (pure, node-testable) — every filter the service
  // sends is a key segment there, ratcheted by its test against the frozen-filter bug.
  discover: (filters?: DiscoverFilters) => discoverKey(filters),
  // Request-identity keys: the key embeds the SAME cleaned record `services` sends as the query
  // string (lib/request-params), so a param that reaches the wire is always part of the cache
  // key. The previous hand-enumerated tuples drifted from the request types (search was missing
  // country/limit/dateFrom/dateTo/source; stories was missing limit/dateFrom/dateTo), which froze
  // filter changes on cached data until a reload. React Query hashes object key-parts with
  // sorted keys, so caller property order is irrelevant.
  stories: (query?: StoryQuery) => ["stories", requestParams(query ?? {})] as const,
  story: (id: string) => ["story", id] as const,
  publisher: (name: string) => ["publisher", name] as const,
  storyIntelligence: (id: string) => ["story-intelligence", id] as const,
  profile: ["profile"] as const,
  saved: ["saved"] as const,
  settings: ["settings"] as const,
  pushConfig: ["push-config"] as const,
  notifications: ["notifications"] as const,
  analytics: ["analytics"] as const,
  coach: ["coach"] as const,
  search: (params: SearchParams) => ["search", requestParams(params)] as const,
  apiTokens: ["apiTokens"] as const,
  // Distinct top-level key (NOT under ["recommendations"]) so a slider-save invalidation of the feed
  // never churns the persisted-feedback cache the page reads to keep ignored cards dismissed.
  recommendationFeedback: ["recommendation-feedback"] as const,
  placeCountries: ["place-countries"] as const,
  geography: ["geography"] as const,
};
