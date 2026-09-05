import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { queryKeys, services } from "@ih/core/api/services";
import type { DiscoverFilters } from "@ih/core/logic/discover-params";
import type {
  FeedbackAction,
  NotificationItem,
  Profile,
  RecFeedbackType,
  Recommendation,
  SavableArticle,
  SavedArticle,
  SearchParams,
  Settings,
  StoryQuery,
} from "@ih/core/domain/types";

import { useAuth } from "./auth-context.tsx";
import { recordRead, type RecordReadInput } from "./record-read.ts";

/**
 * React Query hooks — the only way screens read/write server state.
 *
 * A hook-for-hook mirror of `web/hooks/use-data.ts`: the same keys (from `@ih/core`), the same
 * services, the same invalidations and the same optimistic paths, so a save made on the phone
 * behaves exactly as one made in the browser. What differs is only the platform half the web reads
 * from NextAuth — "is somebody signed in?" — which here comes from the keystore session.
 *
 * Not imported from `web/`: the boundary test forbids it, and the reason is real — that file
 * imports `next-auth/react`, which does not exist in a Metro bundle.
 */

const notFound = (error: unknown) => (error as { status?: number } | null)?.status === 404;

export const useDashboard = () => useQuery({ queryKey: queryKeys.dashboard, queryFn: services.dashboard });
export const useDiscover = (filters?: DiscoverFilters) =>
  useQuery({ queryKey: queryKeys.discover(filters), queryFn: () => services.discover(filters) });
export const useStories = (query?: StoryQuery, opts?: { enabled?: boolean }) =>
  useQuery({
    queryKey: queryKeys.stories(query),
    queryFn: () => services.stories(query),
    enabled: opts?.enabled ?? true,
  });
/** One Story. A 404 is a real, permanent answer — the event dissolved when the catalog window
 *  moved past it — so it is surfaced immediately, never retried. */
export const useStory = (id: string) =>
  useQuery({
    queryKey: queryKeys.story(id),
    queryFn: () => services.story(id),
    enabled: !!id,
    retry: (count, error) => !notFound(error) && count < 3,
  });
export const useSimilarStories = (id: string, limit?: number) =>
  useQuery({
    queryKey: queryKeys.similarStories(id, limit),
    queryFn: () => services.similarStories(id, limit),
    enabled: !!id,
    retry: (count, error) => !notFound(error) && count < 3,
  });
export const usePublisher = (name: string) =>
  useQuery({
    queryKey: queryKeys.publisher(name),
    queryFn: () => services.publisher(name),
    enabled: !!name,
    retry: (count, error) => !notFound(error) && count < 3,
  });
export const useStoryIntelligence = (id: string) =>
  useQuery({
    queryKey: queryKeys.storyIntelligence(id),
    queryFn: () => services.storyIntelligence(id),
    enabled: !!id,
  });
export const useProfile = () => useQuery({ queryKey: queryKeys.profile, queryFn: services.profile });
export const useSettings = () => {
  const { signedIn } = useAuth();
  // `/api/settings` answers 401 to an anonymous caller; asking before sign-in would only fill the
  // console with the refusal and delay the language fallback the provider already has.
  return useQuery({ queryKey: queryKeys.settings, queryFn: services.settings, enabled: signedIn });
};

/** Persists a preferences patch and updates the settings cache with the normalised server result. */
export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<Settings>) => services.updateSettings(patch),
    onSuccess: (saved) => {
      const prev = qc.getQueryData<Settings>(queryKeys.settings);
      qc.setQueryData(queryKeys.settings, saved);
      if (
        prev?.politicalOpenness !== saved.politicalOpenness ||
        prev?.recommendationStrength !== saved.recommendationStrength
      ) {
        // bare prefix: matches every strategy variant ["recommendations", "all" | "rwe-b" | …]
        qc.invalidateQueries({ queryKey: ["recommendations"] });
      }
      if (prev?.readingGoalMinutes !== saved.readingGoalMinutes) {
        qc.invalidateQueries({ queryKey: queryKeys.dashboard });
      }
    },
  });
}

export const usePushConfig = () =>
  useQuery({ queryKey: queryKeys.pushConfig, queryFn: services.pushConfig, staleTime: Infinity });

/** The signed-in reader's notifications for the header bell (badge + panel). Gated to a session —
 *  anonymous callers have none. Fresh for a minute; refetched on the next mount after that. */
export const useNotifications = () => {
  const { signedIn } = useAuth();
  return useQuery({
    queryKey: queryKeys.notifications,
    queryFn: services.notifications,
    enabled: signedIn,
    staleTime: 60_000,
  });
};

/** Mark one notification seen and update the cache in place (no refetch). */
export function useMarkNotificationSeen() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => services.markNotificationSeen(id),
    onSuccess: (_res, id) => {
      qc.setQueryData<NotificationItem[]>(queryKeys.notifications, (prev) =>
        prev
          ? prev.map((n) => (n.id === id && !n.seenAt ? { ...n, seenAt: new Date().toISOString() } : n))
          : prev,
      );
    },
  });
}

export const useRecommendations = (strategy?: Recommendation["strategy"]) =>
  useQuery({
    queryKey: queryKeys.recommendations(strategy),
    queryFn: () => services.recommendations(strategy),
  });

export const useRecommendationExplain = (enabled: boolean) =>
  useQuery({
    queryKey: queryKeys.recommendationExplain,
    queryFn: services.recommendationExplain,
    enabled,
  });

export function useFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ articleId, action }: { articleId: string; action: FeedbackAction }) =>
      services.sendFeedback(articleId, action),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.recommendationFeedback });
      qc.invalidateQueries({ queryKey: queryKeys.feedbackEffects });
    },
  });
}

export function useRemoveFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ articleId, feedback }: { articleId: string; feedback?: RecFeedbackType }) =>
      services.removeFeedback(articleId, feedback),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.recommendationFeedback });
      qc.invalidateQueries({ queryKey: queryKeys.feedbackEffects });
    },
  });
}

export const useFeedbackEffects = () => {
  const { signedIn } = useAuth();
  return useQuery({
    queryKey: queryKeys.feedbackEffects,
    queryFn: services.feedbackEffects,
    enabled: signedIn,
    staleTime: 60_000,
  });
};

export const useRecommendationFeedback = () => {
  const { signedIn } = useAuth();
  return useQuery({
    queryKey: queryKeys.recommendationFeedback,
    queryFn: services.recommendationFeedback,
    enabled: signedIn,
    staleTime: 60_000,
  });
};

/**
 * Records an in-app read into the canonical `/api/me/reads` pipeline, then refreshes the
 * read-derived views. Fire-and-forget for the caller — the button opens the publisher immediately.
 * The feed is marked stale (bare prefix, every strategy variant) so the next visit reflects it.
 */
export function useRecordRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: RecordReadInput) => {
      await recordRead(input);
    },
    onSettled: () => {
      for (const key of [queryKeys.history, queryKeys.dashboard, queryKeys.analytics, queryKeys.report]) {
        qc.invalidateQueries({ queryKey: key });
      }
      qc.invalidateQueries({ queryKey: ["recommendations"] });
    },
  });
}

export const useSaved = () => {
  const { signedIn } = useAuth();
  return useQuery({ queryKey: queryKeys.saved, queryFn: services.saved, enabled: signedIn });
};

/** Save an article — optimistic: the button flips and the profile's Saved counter increments
 *  immediately; on failure both roll back. */
export function useSaveArticle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (article: SavableArticle) => services.saveArticle(article),
    onMutate: async (article) => {
      await Promise.all([
        qc.cancelQueries({ queryKey: queryKeys.saved }),
        qc.cancelQueries({ queryKey: queryKeys.profile }),
      ]);
      const prevSaved = qc.getQueryData<SavedArticle[]>(queryKeys.saved);
      const prevProfile = qc.getQueryData<Profile>(queryKeys.profile);
      const already = (prevSaved ?? []).some((s) => s.articleId === article.id);
      if (!already) {
        qc.setQueryData<SavedArticle[]>(queryKeys.saved, (old) => [
          { articleId: article.id, article, savedAt: new Date().toISOString() },
          ...(old ?? []),
        ]);
        qc.setQueryData<Profile>(queryKeys.profile, (old) =>
          old ? { ...old, savedCount: old.savedCount + 1 } : old,
        );
      }
      return { prevSaved, prevProfile };
    },
    onError: (_e, _article, ctx) => {
      if (ctx?.prevSaved !== undefined) qc.setQueryData(queryKeys.saved, ctx.prevSaved);
      if (ctx?.prevProfile !== undefined) qc.setQueryData(queryKeys.profile, ctx.prevProfile);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.saved });
      qc.invalidateQueries({ queryKey: queryKeys.profile });
    },
  });
}

export function useUnsaveArticle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (articleId: string) => services.unsaveArticle(articleId),
    onMutate: async (articleId) => {
      await Promise.all([
        qc.cancelQueries({ queryKey: queryKeys.saved }),
        qc.cancelQueries({ queryKey: queryKeys.profile }),
      ]);
      const prevSaved = qc.getQueryData<SavedArticle[]>(queryKeys.saved);
      const prevProfile = qc.getQueryData<Profile>(queryKeys.profile);
      const existed = (prevSaved ?? []).some((s) => s.articleId === articleId);
      if (existed) {
        qc.setQueryData<SavedArticle[]>(queryKeys.saved, (old) =>
          (old ?? []).filter((s) => s.articleId !== articleId),
        );
        qc.setQueryData<Profile>(queryKeys.profile, (old) =>
          old ? { ...old, savedCount: Math.max(0, old.savedCount - 1) } : old,
        );
      }
      return { prevSaved, prevProfile };
    },
    onError: (_e, _articleId, ctx) => {
      if (ctx?.prevSaved !== undefined) qc.setQueryData(queryKeys.saved, ctx.prevSaved);
      if (ctx?.prevProfile !== undefined) qc.setQueryData(queryKeys.profile, ctx.prevProfile);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.saved });
      qc.invalidateQueries({ queryKey: queryKeys.profile });
    },
  });
}

/** Records that the reader opened a recommended article (the Open-Mindedness reception signal). */
export function useOpenRecommendation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ articleId, crossCutting }: { articleId: string; crossCutting: boolean }) =>
      services.openRecommendation(articleId, crossCutting),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.report });
      qc.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
  });
}

export function useSearch(params: SearchParams, enabled = true) {
  return useQuery({
    queryKey: queryKeys.search(params),
    queryFn: () => services.search(params),
    enabled,
  });
}

export const usePlaceCountries = () =>
  useQuery({ queryKey: queryKeys.placeCountries, queryFn: services.placeCountries });
