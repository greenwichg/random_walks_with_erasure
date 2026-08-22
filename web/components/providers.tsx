"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { ThemeProvider } from "next-themes";
import { useState, type ReactNode } from "react";
import type { Session } from "next-auth";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LanguageProvider } from "@/lib/i18n";
import { AnalyticsListener } from "@/components/analytics-listener";
import { RumListener } from "@/components/rum-listener";
import { ServiceWorkerRegistrar } from "@/components/pwa/service-worker-registrar";
import { ShellPrefetch } from "@/components/shell-prefetch";
import { configureApi } from "@ih/core/api/client";

/**
 * Point the shared API client at this deployment.
 *
 * `@ih/core` cannot read `process.env.NEXT_PUBLIC_API_BASE_URL` itself — `process` does not exist on
 * React Native and `NEXT_PUBLIC_*` is a Next build-time substitution — so the base URL is injected by
 * whichever app is hosting the client. This is the web's injection point, and the value is exactly
 * what `services/api.ts` used to read at module load: unset means same-origin `/api/*`.
 *
 * At module scope rather than inside the component, so it runs once when the client bundle loads
 * rather than on every mount, and before any hook can fire a request.
 *
 * No `getToken`: the browser has a session cookie and the API resolves a session before it looks at
 * any bearer token (docs/API_AUTH_MATRIX.md). The Expo app supplies one here instead.
 */
configureApi({ baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "" });

/**
 * App-wide client providers: theme (light/dark/system), React Query, and the
 * shared Radix tooltip context. Mounted once in the root layout.
 *
 * `session` arrives from the server (R1a): seeding `SessionProvider` with it makes the client's
 * session state authoritative at FIRST RENDER, so nothing that gates on `useSession().status`
 * waits for a network round trip to learn what the server knew when it rendered the page.
 */
export function Providers({ children, session }: { children: ReactNode; session?: Session | null }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );

  return (
    <SessionProvider session={session}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
        <QueryClientProvider client={queryClient}>
          <LanguageProvider>
            <TooltipProvider delayDuration={200}>
              <AnalyticsListener />
              <RumListener />
              {/* Registers /sw.js independently of push, which is what makes the app installable:
                  push registration is gated on RWE_PUSH_ENABLED and production runs with it off. */}
              <ServiceWorkerRegistrar />
              <ShellPrefetch hasSession={!!session} />
              {children}
            </TooltipProvider>
          </LanguageProvider>
        </QueryClientProvider>
      </ThemeProvider>
    </SessionProvider>
  );
}
