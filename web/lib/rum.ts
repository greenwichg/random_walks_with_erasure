/**
 * RUM (real-user monitoring) — the pure half. No DOM, no observers, so it runs under `node --test`;
 * everything here is a function of its arguments. The browser side (`components/rum-listener.tsx`)
 * collects; this module normalises, clamps, and attributes.
 *
 * This exists because the performance investigation ended on an unresolved question its server-side
 * evidence could not answer: whether healthy-state pages feel slow because of the proxy/waterfall
 * architecture, or only because incident windows coloured perception. Every claim in that report
 * about *user-perceived* latency was flagged "no RUM exists at all" — this is the RUM.
 */

/** One collected event. `t` discriminates; unused fields stay absent, never null-padded. */
export interface RumEvent {
  t: "vital" | "api" | "longtask" | "nav" | "route";
  /** Metric name for vitals (TTFB/FCP/LCP/CLS/INP/FID or Next.js-hydration etc.). */
  name?: string;
  /** Metric value (ms; CLS is unitless). */
  value?: number;
  /** Route template the event happened on (see {@link routeTemplate}). */
  path?: string;
  /** API resource: request path template + duration + start offset (ms since timeOrigin). */
  api?: string;
  ms?: number;
  start?: number;
  /** Transfer size in bytes for API resources (0 when served from cache). */
  size?: number;
  /** Wall-clock ms since epoch at record time. */
  ts?: number;
}

/**
 * Collapse a concrete pathname to its route template, so the log stream aggregates by PAGE and a
 * story id or publisher name never becomes its own metric series (unbounded cardinality is how a
 * metrics pipeline dies). Unknown paths pass through — at this app's size that is bounded by the
 * route tree, and passing through is more honest than inventing an [unknown] bucket.
 */
const TEMPLATES: Array<[RegExp, string]> = [
  [/^\/stories\/[^/]+$/, "/stories/[id]"],
  [/^\/publishers\/[^/]+$/, "/publishers/[name]"],
];

export function routeTemplate(pathname: string): string {
  const path = (pathname || "/").split(/[?#]/, 1)[0] || "/";
  for (const [re, template] of TEMPLATES) {
    if (re.test(path)) return template;
  }
  return path;
}

/** Same idea for API request URLs: origin gone, query gone, dynamic segments templated. */
const API_TEMPLATES: Array<[RegExp, string]> = [
  [/^\/api\/stories\/[^/]+$/, "/api/stories/[id]"],
  [/^\/api\/publishers\/[^/]+$/, "/api/publishers/[name]"],
  [/^\/api\/auth\/.*$/, "/api/auth/*"],
];

export function apiPath(url: string): string | null {
  let path: string;
  try {
    path = new URL(url, "http://x").pathname;
  } catch {
    return null;
  }
  if (!path.startsWith("/api/")) return null;
  for (const [re, template] of API_TEMPLATES) {
    if (re.test(path)) return template;
  }
  return path;
}

/** Field whitelist + length/size caps for a beacon batch — the sink trusts nothing it receives. */
export function clampEvents(raw: unknown, maxEvents = 100): RumEvent[] {
  if (!Array.isArray(raw)) return [];
  const out: RumEvent[] = [];
  for (const item of raw.slice(0, maxEvents)) {
    if (typeof item !== "object" || item === null) continue;
    const e = item as Record<string, unknown>;
    if (e.t !== "vital" && e.t !== "api" && e.t !== "longtask" && e.t !== "nav" && e.t !== "route") continue;
    const keep: RumEvent = { t: e.t };
    if (typeof e.name === "string") keep.name = e.name.slice(0, 60);
    if (typeof e.path === "string") keep.path = e.path.slice(0, 200);
    if (typeof e.api === "string") keep.api = e.api.slice(0, 200);
    for (const k of ["value", "ms", "start", "ts", "size"] as const) {
      const v = e[k];
      if (typeof v === "number" && Number.isFinite(v)) keep[k] = Math.round(v * 10) / 10;
    }
    out.push(keep);
  }
  return out;
}

/**
 * Attribute one page-load's time to its plausible owners, coarsely and honestly.
 *
 * This is arithmetic over overlapping phases, not a flame graph: TTFB is pure network+server; the
 * API wall is the span from the first `/api/` request start to the last response end (requests
 * overlap, so summing durations would double-count — the WALL is what a reader waits through);
 * hydration is Next's own measure; `longtaskMs` beyond hydration approximates other main-thread
 * JS/render cost. The dominant bucket is a *lead*, not a verdict — the report reads these next to
 * the waterfall, never instead of it.
 */
export interface LoadSnapshot {
  ttfb?: number;
  lcp?: number;
  hydrationMs?: number;
  apiWallMs?: number;
  apiCount?: number;
  longtaskMs?: number;
}

export function attributeLoad(s: LoadSnapshot): Array<[string, number]> {
  const ttfb = Math.max(0, s.ttfb ?? 0);
  const hydration = Math.max(0, s.hydrationMs ?? 0);
  const api = Math.max(0, s.apiWallMs ?? 0);
  const render = Math.max(0, (s.longtaskMs ?? 0) - hydration);
  const buckets: Array<[string, number]> = [
    ["network-ttfb", ttfb],
    ["api", api],
    ["hydration", hydration],
    ["render-js", render],
  ];
  return buckets.sort((a, b) => b[1] - a[1]);
}

/** The dominant bucket's name, or "quiet" when nothing measured above the floor (ms). */
export function dominantBottleneck(s: LoadSnapshot, floorMs = 50): string {
  const [top] = attributeLoad(s);
  return top && top[1] >= floorMs ? top[0] : "quiet";
}
