"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys, services } from "@/services";
import type { FeedbackAction, Recommendation } from "@/types/domain";

/**
 * React Query hooks — the only way components read/write server state. Thin
 * wrappers over `services` so screens stay declarative and caching is uniform.
 */

export const useDashboard = () => useQuery({ queryKey: queryKeys.dashboard, queryFn: services.dashboard });
export const useReport = () => useQuery({ queryKey: queryKeys.report, queryFn: services.report });
export const useHistory = () => useQuery({ queryKey: queryKeys.history, queryFn: services.history });
export const useTopics = () => useQuery({ queryKey: queryKeys.topics, queryFn: services.topics });
export const useStories = () => useQuery({ queryKey: queryKeys.stories, queryFn: services.stories });
export const useStory = (id: string) =>
  useQuery({ queryKey: queryKeys.story(id), queryFn: () => services.story(id), enabled: !!id });
export const useProfile = () => useQuery({ queryKey: queryKeys.profile, queryFn: services.profile });
export const useSettings = () => useQuery({ queryKey: queryKeys.settings, queryFn: services.settings });
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

export function useSearch(q: string) {
  return useQuery({
    queryKey: queryKeys.search(q),
    queryFn: () => services.search(q),
    enabled: q.trim().length > 1,
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
