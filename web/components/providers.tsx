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
