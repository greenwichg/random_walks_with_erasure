"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { queryKeys, services } from "@/services";

/**
 * R1a — fire the app shell's data queries at the earliest client moment, in parallel, un-gated.
 *
 * The RUM evidence this answers (frontend performance report, F1): every hard load paid a
 * ~6-request "shell tax" serialized into 3–4 phases — session at ~337 ms, the dashboard/settings
 * burst at ~370 ms, notifications at ~430 ms — because each request waited for whatever mounted or
 * resolved before it. The decomposition probes put ~60–65% of the tax on that staggering rather
 * than on the requests themselves, and in production every phase costs a full round trip.
 *
 * This component collapses the phases: it mounts with the providers (the same commit that starts
 * the push reconciler at ~311 ms — the measured earliest) and prefetches the three shell queries
 * through the SHARED QueryClient. The sidebar's `useDashboard`, the settings surfaces'
 * `useSettings`, and the header's `useNotifications` then adopt the already-in-flight promises by
 * key — React Query deduplicates, so nothing fetches twice and no consumer changes.
 *
 * `hasSession` comes from the server-rendered session (root layout), not from `useSession()` —
 * asking the client hook would reintroduce the exact wait this exists to remove. Anonymous pages
 * (onboarding, sign-in) prefetch nothing: their shells render none of these surfaces, and firing
 * authenticated reads for visitors would be load without a reader.
 *
 * Deliberately NOT prefetched: the page's own query (unknowable here — owned by the page),
 * `/api/push/config` (the reconciler already fires it in this same commit), and anything
 * mutation-adjacent. This list is the app SHELL, and only the shell.
 */
export function ShellPrefetch({ hasSession }: { hasSession: boolean }) {
  const queryClient = useQueryClient();
  const fired = React.useRef(false);

  React.useEffect(() => {
    if (!hasSession || fired.current) return;
    fired.current = true;               // once per app load; StrictMode double-mount included
    void queryClient.prefetchQuery({ queryKey: queryKeys.dashboard, queryFn: services.dashboard });
    void queryClient.prefetchQuery({ queryKey: queryKeys.settings, queryFn: services.settings });
    void queryClient.prefetchQuery({
      queryKey: queryKeys.notifications,
      queryFn: services.notifications,
      // Match the consumer's staleTime (`useNotifications`): a prefetch that expired before the
      // header mounted would fetch twice — the opposite of the point.
      staleTime: 60_000,
    });
  }, [hasSession, queryClient]);

  return null;
}
