"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys, services } from "@/services";
import type { FeedbackAction, Recommendation, SearchParams, StoryQuery } from "@/types/domain";

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

/** Persists a preferences patch and updates the settings cache with the normalised server result. */
export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<import("@/types/domain").Settings>) => services.updateSettings(patch),
    onSuccess: (saved) => qc.setQueryData(queryKeys.settings, saved),
  });
}
export const useAnalytics = () => useQuery({ queryKey: queryKeys.analytics, queryFn: services.analytics });

export const useRecommendations = (strategy?: Recommendation["strategy"]) =>
  useQuery({
    queryKey: queryKeys.recommendations(strategy),
    queryFn: () => services.recommendations(strategy),
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
