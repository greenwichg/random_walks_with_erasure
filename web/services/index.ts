import { getJson, postJson, deleteJson } from "@/services/api";
import type {
  AnalyticsSeries,
  ApiToken,
  ApiTokenMint,
  CoachMessage,
  DashboardSummary,
  DiscoverResponse,
  FeedbackAction,
  MeasuredHealthReport,
  HistoryEntry,
  Profile,
  Recommendation,
  RecommendationReception,
  SearchParams,
  SearchResponse,
  Settings,
  StoriesResponse,
  Story,
  StoryQuery,
  TopicSlice,
} from "@/types/domain";

/**
 * Typed data-access layer. Every screen imports from here — never from axios
 * directly — so the transport is centralised and the return types are enforced.
 * Each function maps 1:1 to a backend endpoint (mock today, Python tomorrow).
 */
export const services = {
  dashboard: () => getJson<DashboardSummary>("/dashboard"),
  report: () => getJson<MeasuredHealthReport>("/report"),
  recommendations: (strategy?: Recommendation["strategy"]) =>
    getJson<Recommendation[]>("/recommendations", strategy ? { strategy } : undefined),
  sendFeedback: (articleId: string, action: FeedbackAction) =>
    postJson<{ ok: boolean }>("/recommendations", { articleId, action }),
  // Record that the reader opened a recommended article — the Open-Mindedness reception signal.
  openRecommendation: (articleId: string, crossCutting: boolean) =>
    postJson<RecommendationReception>("/me/recommendations/opened", { articleId, crossCutting }),
  history: () => getJson<HistoryEntry[]>("/history"),
  topics: () => getJson<TopicSlice[]>("/topics"),
  // Discover: latest catalog articles + facets, with optional topic/publisher/lean filters.
  discover: (filters?: { topic?: string; publisher?: string; lean?: string }) =>
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
  profile: () => getJson<Profile>("/profile"),
  settings: () => getJson<Settings>("/settings"),
  // Persist a (partial) preferences patch; the engine merges + returns the full normalised settings.
  updateSettings: (patch: Partial<Settings>) => postJson<Settings>("/settings", patch),
  analytics: () => getJson<AnalyticsSeries>("/analytics"),
  coachHistory: () => getJson<CoachMessage[]>("/coach"),
  coachSend: (message: string) => postJson<CoachMessage>("/coach", { message }),
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
};

/** React Query cache keys, colocated so invalidation stays consistent. */
export const queryKeys = {
  dashboard: ["dashboard"] as const,
  report: ["report"] as const,
  recommendations: (strategy?: string) => ["recommendations", strategy ?? "all"] as const,
  history: ["history"] as const,
  topics: ["topics"] as const,
  discover: (filters?: { topic?: string; publisher?: string; lean?: string }) =>
    ["discover", filters?.topic ?? "all", filters?.publisher ?? "all", filters?.lean ?? "all"] as const,
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
  profile: ["profile"] as const,
  settings: ["settings"] as const,
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
};
