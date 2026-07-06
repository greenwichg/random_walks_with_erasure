/**
 * Browser security headers for the web tier — pure logic so it can be unit-tested (node --test)
 * and consumed from next.config.mjs's `headers()`. Production-first defaults; relaxed in local
 * development so `next dev` (HMR websocket + React Fast Refresh) keeps working.
 *
 * Note: Next serializes `headers()` at BUILD time (`next build` bakes them into the routes
 * manifest; `next start` does not re-evaluate). So gating keys on `NODE_ENV`, which is the reliable
 * signal at build/evaluation time: a production build (`next build`, `NODE_ENV=production`) bakes in
 * the strict CSP + HSTS; `next dev` (`NODE_ENV=development`) evaluates live and gets the relaxations
 * HMR needs. (`RWE_ENV` isn't reliably set at build time, so it isn't used here.)
 */

export function isProduction(env) {
  return env.NODE_ENV === "production";
}

/**
 * Build the Content-Security-Policy string. Compatible with Next.js (its inline hydration
 * scripts/styles need `'unsafe-inline'`; a nonce-based CSP is future work), Google OAuth (a
 * top-level redirect, not an embed — so no google origins are needed), and the browser extension
 * (a privileged cross-origin fetch that our page CSP does not govern).
 */
export function buildCsp({ dev = false, apiBase = "", override = "" } = {}) {
  if (override) return override;
  const scriptSrc = ["'self'", "'unsafe-inline'"];
  if (dev) scriptSrc.push("'unsafe-eval'"); // React Fast Refresh under `next dev`
  const connectSrc = ["'self'"];
  if (apiBase) connectSrc.push(apiBase); // when the browser calls a different API origin
  if (dev) connectSrc.push("ws:", "wss:"); // HMR websocket under `next dev`

  const directives = {
    "default-src": ["'self'"],
    "base-uri": ["'self'"],
    "object-src": ["'none'"],
    "frame-ancestors": ["'none'"], // anti-clickjacking (with X-Frame-Options as a fallback)
    "form-action": ["'self'"],
    "img-src": ["'self'", "data:", "https:"], // publisher/article images + inline data URIs
    "font-src": ["'self'", "data:"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "script-src": scriptSrc,
    "connect-src": connectSrc,
  };
  return Object.entries(directives)
    .map(([k, v]) => `${k} ${v.join(" ")}`)
    .join("; ");
}

/**
 * The header sets for page responses and API responses. Returns `{ page, api }` as Next-style
 * `{ key, value }` arrays. `env` defaults to `process.env`.
 */
export function securityHeaders(env = process.env) {
  const production = env.NODE_ENV === "production";
  const dev = !production;
  const cspDisabled = ["1", "true", "yes"].includes((env.RWE_DISABLE_CSP || "").toLowerCase());
  const csp = cspDisabled
    ? null
    : buildCsp({ dev, apiBase: env.NEXT_PUBLIC_API_BASE_URL || "", override: env.RWE_CSP || "" });

  // Pages + static assets (everything except /api/*).
  const page = [
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), browsing-topics=()" },
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
    { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  ];
  if (csp) page.unshift({ key: "Content-Security-Policy", value: csp });
  if (production) page.push({ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" });

  // Authenticated JSON APIs: never cache; still no-sniff. Deliberately NO Cross-Origin-Resource-Policy
  // here, so the browser extension's privileged cross-origin fetch to /api/me/reads is never blocked.
  const api = [
    { key: "Cache-Control", value: "no-store" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  ];

  return { page, api, production, cspEnabled: Boolean(csp) };
}
