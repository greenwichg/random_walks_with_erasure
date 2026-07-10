"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys, services } from "@/services";
import { recordRead, type RecordReadInput } from "@/lib/record-read";
import type {
  FeedbackAction,
  Profile,
  Recommendation,
  SavableArticle,
  SavedArticle,
  SearchParams,
  Settings,
  StoryQuery,
} from "@/types/domain";

/**
 * React Query hooks — the only way components read/write server state. Thin
 * wrappers over `services` so screens stay declarative and caching is uniform.
 */

export const useDashboard = () => useQuery({ queryKey: queryKeys.dashboard, queryFn: services.dashboard });
export const useReport = () => useQuery({ queryKey: queryKeys.report, queryFn: services.report });
export const useHistory = () => useQuery({ queryKey: queryKeys.history, queryFn: services.history });
export const useTopics = () => useQuery({ queryKey: queryKeys.topics, queryFn: services.topics });
export const useDiscover = (filters?: { topic?: string; publisher?: string; lean?: string; limit?: number }) =>
  useQuery({ queryKey: queryKeys.discover(filters), queryFn: () => services.discover(filters) });
export const useStories = (query?: StoryQuery) =>
  useQuery({ queryKey: queryKeys.stories(query), queryFn: () => services.stories(query) });
export const useStory = (id: string) =>
  useQuery({ queryKey: queryKeys.story(id), queryFn: () => services.story(id), enabled: !!id });
/** Deterministic Story Intelligence for one event (freshness / lifecycle / momentum / timeline / alerts). */
export const useStoryIntelligence = (id: string) =>
  useQuery({
    queryKey: queryKeys.storyIntelligence(id),
    queryFn: () => services.storyIntelligence(id),
    enabled: !!id,
  });
export const useProfile = () => useQuery({ queryKey: queryKeys.profile, queryFn: services.profile });
export const useSettings = () => useQuery({ queryKey: queryKeys.settings, queryFn: services.settings });

/** Persists a preferences patch and updates the settings cache with the normalised server result.
 * The recommendation sliders are per-request recommender parameters and the reading goal feeds the
 * dashboard's today-vs-goal card, so a save that changed them marks those caches stale — the query
 * refetches on next mount (e.g. navigating back to Recommendations), no hard refresh needed. */
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
export const useAnalytics = () => useQuery({ queryKey: queryKeys.analytics, queryFn: services.analytics });

export const useRecommendations = (strategy?: Recommendation["strategy"]) =>
  useQuery({
    queryKey: queryKeys.recommendations(strategy),
    queryFn: () => services.recommendations(strategy),
  });

/** The evidence behind the card's "Why?" drawer (21a.2). Lazy by design (D1): fetched only when
 * a drawer first opens, then cached and shared by every card of the feed. Keyed under the
 * ["recommendations"] prefix so a slider save invalidates it together with the feed it explains. */
export const useRecommendationExplain = (enabled: boolean) =>
  useQuery({
    queryKey: queryKeys.recommendationExplain,
    queryFn: services.recommendationExplain,
    enabled,
  });

export const useCoachHistory = () =>
  useQuery({ queryKey: queryKeys.coach, queryFn: services.coachHistory });

export function useFeedback() {
  return useMutation({
    mutationFn: ({ articleId, action }: { articleId: string; action: FeedbackAction }) =>
      services.sendFeedback(articleId, action),
  });
}

/**
 * Records an in-app read (the PRIMARY reading source) into the canonical `/api/me/reads` pipeline,
 * then refreshes the read-derived views. Fire-and-forget for the caller — the button opens the
 * publisher immediately; a short grace lets the beacon persist before we invalidate history /
 * dashboard / analytics / report, so the new read appears without a manual reload. No optimistic
 * write (a read must be the server's truth), and only read-derived queries are touched.
 */
export function useRecordRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: RecordReadInput) => {
      recordRead(input);
      await new Promise((resolve) => setTimeout(resolve, 700)); // let the beacon reach + persist
    },
    onSettled: () => {
      for (const key of [queryKeys.history, queryKeys.dashboard, queryKeys.analytics, queryKeys.report]) {
        qc.invalidateQueries({ queryKey: key });
      }
    },
  });
}

/** The signed-in reader's saved articles (persisted). Backs the shared SaveButton's saved-state so
 *  every button on a page reflects the same server truth, and survives a refresh. */
export const useSaved = () => useQuery({ queryKey: queryKeys.saved, queryFn: services.saved });

/**
 * Save an article — optimistic: the SaveButton flips and the profile's Saved counter increments
 * immediately; on failure both roll back. Only the `saved` + `profile` queries are touched (never a
 * full reload), and `onSettled` refetches them so multiple tabs converge on the real server count.
 */
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

/** Unsave an article — the mirror of {@link useSaveArticle}: optimistic removal + counter decrement,
 *  rollback on failure, and the same minimal `saved` + `profile` invalidation. */
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

/**
 * Records that the reader opened a recommended article (the Open-Mindedness reception signal).
 * On success it refreshes the report + dashboard so Open-Mindedness — which populates from
 * cross-cutting recommendation reception — appears/updates without a manual reload.
 */
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

/** The signed-in user's browser-extension API tokens (metadata only). */
export const useApiTokens = () =>
  useQuery({ queryKey: queryKeys.apiTokens, queryFn: services.apiTokens });

/** Mints a token; refreshes the list so the new token appears. */
export function useCreateApiToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (label?: string) => services.createApiToken(label),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.apiTokens }),
  });
}

/** Revokes a token; refreshes the list. */
export function useRevokeApiToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => services.revokeApiToken(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.apiTokens }),
  });
}

/** Sends a coach message and appends the reply to the cached transcript. */
export function useCoachSend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (message: string) => services.coachSend(message),
    onSuccess: (reply) => {
      qc.setQueryData<import("@/types/domain").CoachMessage[]>(queryKeys.coach, (prev) => [
        ...(prev ?? []),
        reply,
      ]);
    },
  });
}
