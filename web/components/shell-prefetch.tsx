"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { queryKeys, services } from "@ih/core/api/services";

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
 * through the SHARED QueryClient. The home rail's `useDashboard`, the settings surfaces'
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
    // R1b: ONE round trip for the whole shell. The mechanism matters more than the endpoint: a
    // seed applied AFTER the response arrives loses the race — the home rail's, header's and
    // reconciler's hooks fire their own queryFns in this same commit, milliseconds from now. So
    // each shell key gets an in-flight PREFETCH registered synchronously here, whose queryFn
    // resolves from one shared bootstrap promise; a consumer mounting later joins the pending
    // query instead of fetching. (The first draft used setQueryData-on-arrival and the RUM
    // harness showed five shell requests riding alongside the bootstrap — measured, not assumed.)
    //
    // Resilience is per section, inside the queryFn: a null section — or a failed bootstrap,
    // which nulls them all — falls back to that section's own service call, so a broken aggregate
    // degrades to exactly R1a's parallel prefetches and never worse.
    interface BootstrapBody {
      dashboard?: unknown; settings?: unknown; notifications?: unknown; pushConfig?: unknown;
    }
    const bootstrap: Promise<BootstrapBody | null> = fetch("/api/bootstrap")
      .then((res) => (res.ok ? (res.json() as Promise<BootstrapBody>) : null))
      .catch(() => null);
    const section = async <T,>(key: keyof BootstrapBody, fallback: () => Promise<T>): Promise<T> => {
      const body = await bootstrap;
      const value = body?.[key];
      return value != null ? (value as T) : fallback();
    };
    void queryClient.prefetchQuery({
      queryKey: queryKeys.dashboard,
      queryFn: () => section("dashboard", services.dashboard),
    });
    void queryClient.prefetchQuery({
      queryKey: queryKeys.settings,
      queryFn: () => section("settings", services.settings),
    });
    void queryClient.prefetchQuery({
      queryKey: queryKeys.notifications,
      queryFn: () => section("notifications", services.notifications),
      // Match the consumer's staleTime (`useNotifications`): a prefetch that expired before the
      // header mounted would fetch twice — the opposite of the point.
      staleTime: 60_000,
    });
    void queryClient.prefetchQuery({
      queryKey: queryKeys.pushConfig,
      queryFn: () => section("pushConfig", services.pushConfig),
      staleTime: 300_000,               // matches usePushConfig
    });
  }, [hasSession, queryClient]);

  return null;
}
