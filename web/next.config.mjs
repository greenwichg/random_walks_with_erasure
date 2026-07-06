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
};

export default nextConfig;
