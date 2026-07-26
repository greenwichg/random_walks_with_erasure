import { securityHeaders } from "./lib/security-headers.mjs";

// Computed once at server start (reads env then): CSP + hardening for pages, no-store for APIs.
const { page: pageHeaders, api: apiHeaders } = securityHeaders(process.env);

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Run instrumentation.ts's register() at server boot — used for fail-fast env validation.
  experimental: { instrumentationHook: true },
  // Images from arbitrary publishers/CDNs; loosen when the real backend fixes a set.
  images: {
    remotePatterns: [{ protocol: "https", hostname: "**" }],
  },
  // Lets a real backend base URL be injected at build/deploy time (see services/api.ts).
  env: {
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL ?? "",
  },
  // Consolidation history: Local v1 folded into Countries, then Countries folded into Stories
  // (its country filter). Both paths land on /stories; `/local` stays reserved for the future
  // personalized local experience (docs/LOCATION_PLATFORM.md). Temporary redirects on purpose —
  // browsers must not cache a permanent answer for paths that may return as their own pages.
  async redirects() {
    return [
      { source: "/local", destination: "/stories", permanent: false },
      { source: "/countries", destination: "/stories", permanent: false },
    ];
  },
  async headers() {
    return [
      // Pages + static assets (everything except /api/*): CSP, frame/opener/resource isolation, etc.
      { source: "/((?!api/).*)", headers: pageHeaders },
      // Authenticated JSON APIs: no-store + no-sniff (no CORP here — see security-headers.mjs).
      { source: "/api/:path*", headers: apiHeaders },
    ];
  },
};

export default nextConfig;
