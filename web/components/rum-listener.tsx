"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import { useReportWebVitals } from "next/web-vitals";
import { apiPath, routeTemplate, type RumEvent } from "@/lib/rum";

/**
 * RUM collection (the browser half — the pure half lives in `lib/rum.ts`).
 *
 * Mounted once inside the app providers, renders nothing, and must never affect what the reader
 * sees: every observer is wrapped, every flush is fire-and-forget, and an unsupported API simply
 * collects nothing on that browser.
 *
 * What it records, and why each stream exists:
 *  - **Web Vitals** (TTFB/FCP/LCP/CLS/INP + Next's hydration / render / route-change-to-render)
 *    via `useReportWebVitals` — the user-perceived numbers the performance investigation had zero
 *    of. The Next custom metrics are what let "hydration" and "soft-nav render" be attributed
 *    without a profiling build.
 *  - **`/api/*` resource timings** — the client-side waterfall: count, start offsets and durations
 *    per route template. This is the evidence for the fan-out findings (F6) as the reader
 *    experiences it, not as the proxy logs it.
 *  - **Long tasks** — main-thread blocks ≥50 ms, the raw material of INP and the "is it rendering
 *    or is it network" question.
 *  - **Route changes** — soft-navigation boundaries, so every event carries the page it happened on.
 *
 * Events buffer in `window.__ihRum` (also the measurement API for the local evidence harness) and
 * flush as a beacon to `/api/rum` when the page hides or the buffer fills. The sink writes one
 * structured log line per event — `docker logs deploy-web-1 | grep '"event":"rum"'` is the query
 * surface, same as every other observability stream in this repo.
 */

const FLUSH_AT = 60;
const BUFFER_CAP = 500;

interface RumBuffer {
  events: RumEvent[];
  sessionId: string;
  flush: () => void;
}

declare global {
  interface Window {
    __ihRum?: RumBuffer;
  }
}

function buffer(): RumBuffer {
  const w = window;
  if (!w.__ihRum) {
    w.__ihRum = {
      events: [],
      sessionId: Math.random().toString(36).slice(2, 10),
      flush: () => {
        const buf = w.__ihRum;
        if (!buf || buf.events.length === 0) return;
        const batch = buf.events.splice(0, 100);
        const body = JSON.stringify({ sessionId: buf.sessionId, events: batch });
        try {
          if (navigator.sendBeacon) {
            navigator.sendBeacon("/api/rum", new Blob([body], { type: "application/json" }));
          } else {
            void fetch("/api/rum", {
              method: "POST", body, keepalive: true,
              headers: { "content-type": "application/json" },
            }).catch(() => {});
          }
        } catch {
          /* telemetry must never throw */
        }
      },
    };
  }
  return w.__ihRum;
}

function record(event: RumEvent): void {
  try {
    const buf = buffer();
    if (buf.events.length >= BUFFER_CAP) buf.events.shift();
    buf.events.push({ ...event, ts: Date.now() });
    if (buf.events.length >= FLUSH_AT) buf.flush();
  } catch {
    /* never throw into the app */
  }
}

export function RumListener() {
  const pathname = usePathname();
  const path = routeTemplate(pathname ?? "/");
  const pathRef = React.useRef(path);
  pathRef.current = path;

  // Per-metric-instance memory, and it earns its two maps. The FIRST day of production data showed
  // every page reporting the same two LCP values: the vitals hook re-reports each metric on every
  // soft navigation, and this listener recorded each re-report stamped with the CURRENT route — one
  // hard load smeared across every page the reader visited after it. A metric instance (`metric.id`)
  // therefore gets: its path captured at FIRST sighting (the page it actually measured — the first
  // report always happens there), and a record only when its VALUE changed (a candidate update, e.g.
  // LCP growing or INP worsening — never a soft-nav echo). The id ships in the event so the
  // aggregation can collapse candidates to their final value per (session, id).
  const vitalPath = React.useRef(new Map<string, string>());
  const vitalValue = React.useRef(new Map<string, number>());
  useReportWebVitals((metric) => {
    let measuredPath = vitalPath.current.get(metric.id);
    if (measuredPath === undefined) {
      measuredPath = pathRef.current;
      vitalPath.current.set(metric.id, measuredPath);
    }
    if (vitalValue.current.get(metric.id) === metric.value) return;
    vitalValue.current.set(metric.id, metric.value);
    record({ t: "vital", name: metric.name, value: metric.value,
             id: String(metric.id).slice(-12), path: measuredPath });
  });

  // Soft-navigation boundaries.
  React.useEffect(() => {
    record({ t: "route", path });
  }, [path]);

  // The hard-load navigation entry (TTFB and friends beyond what the vital reports), once.
  React.useEffect(() => {
    try {
      const [nav] = performance.getEntriesByType("navigation") as PerformanceNavigationTiming[];
      if (nav) {
        record({ t: "nav", name: "responseStart", value: nav.responseStart, path: pathRef.current });
        record({ t: "nav", name: "domInteractive", value: nav.domInteractive, path: pathRef.current });
        record({ t: "nav", name: "loadEventEnd", value: nav.loadEventEnd, path: pathRef.current });
      }
    } catch {
      /* no navigation timing — nothing to record */
    }
  }, []);

  // The /api/* waterfall + long tasks. `buffered: true` captures what happened before mount.
  React.useEffect(() => {
    const observers: PerformanceObserver[] = [];
    try {
      const res = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          const api = apiPath(entry.name);
          if (api) {
            const size = (entry as PerformanceResourceTiming).transferSize;
            record({ t: "api", api, ms: entry.duration, start: entry.startTime, size, path: pathRef.current });
          }
        }
      });
      res.observe({ type: "resource", buffered: true });
      observers.push(res);
    } catch {
      /* resource timing unsupported */
    }
    try {
      const lt = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          record({ t: "longtask", ms: entry.duration, start: entry.startTime, path: pathRef.current });
        }
      });
      lt.observe({ type: "longtask", buffered: true });
      observers.push(lt);
    } catch {
      /* longtask unsupported (Safari) — INP still arrives via the vital */
    }
    const onHide = () => {
      if (document.visibilityState === "hidden") buffer().flush();
    };
    document.addEventListener("visibilitychange", onHide);
    return () => {
      observers.forEach((o) => o.disconnect());
      document.removeEventListener("visibilitychange", onHide);
    };
  }, []);

  return null;
}
