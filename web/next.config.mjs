/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
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
