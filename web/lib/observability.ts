/**
 * Frontend error reporting — vendor-agnostic (OBS1).
 *
 * The app reports errors through `reportError()`; the concrete destination is a swappable *provider*, so
 * no vendor is hardcoded. A later phase registers a Sentry / Azure Application Insights / OpenTelemetry /
 * custom-backend provider via `setErrorReporter()` — call sites never change.
 *
 * Defaults, chosen to be safe and quiet:
 *   • development → `consoleProvider` (logs to the console, no network).
 *   • production  → `beaconProvider` posting to the same-origin `/api/client-errors` proxy (which
 *     forwards to the engine's structured logs + reporter), so a browser crash lands in the same
 *     correlatable stream as a server error. It fails silently and never throws.
 */

export interface ErrorContext {
  digest?: string;
  componentStack?: string;
  url?: string;
  [key: string]: unknown;
}

export interface ErrorReporterProvider {
  readonly name: string;
  captureException(error: unknown, context?: ErrorContext): void;
}

/** Dev default: log to the console; no network, never throws. */
export const consoleProvider: ErrorReporterProvider = {
  name: "console",
  captureException(error, context) {
    // eslint-disable-next-line no-console
    console.error("[observability]", error, context ?? {});
  },
};

/** Prod default: best-effort beacon to the same-origin backend sink. Fire-and-forget; swallows errors. */
export const beaconProvider: ErrorReporterProvider = {
  name: "beacon",
  captureException(error, context) {
    try {
      const err = error as { message?: string; name?: string; stack?: string };
      const body = JSON.stringify({
        message: String(err?.message ?? error ?? "Unknown error").slice(0, 500),
        name: err?.name ? String(err.name).slice(0, 120) : undefined,
        stack: err?.stack ? String(err.stack).slice(0, 4000) : undefined,
        digest: context?.digest,
        url: typeof window !== "undefined" ? window.location?.pathname : context?.url,
        context: context?.componentStack ? { componentStack: String(context.componentStack).slice(0, 4000) } : undefined,
      });
      // sendBeacon survives page unload; fall back to fetch(keepalive) where it's unavailable.
      if (typeof navigator !== "undefined" && navigator.sendBeacon) {
        navigator.sendBeacon("/api/client-errors", new Blob([body], { type: "application/json" }));
      } else if (typeof fetch !== "undefined") {
        void fetch("/api/client-errors", { method: "POST", body, keepalive: true, headers: { "content-type": "application/json" } }).catch(() => {});
      }
    } catch {
      /* reporting must never throw */
    }
  },
};

let _provider: ErrorReporterProvider =
  process.env.NODE_ENV === "production" ? beaconProvider : consoleProvider;

/** Swap the reporter (call once at startup to plug in a vendor). */
export function setErrorReporter(provider: ErrorReporterProvider): void {
  _provider = provider;
}

export function currentErrorReporter(): ErrorReporterProvider {
  return _provider;
}

/** Report an error through the active provider. Best-effort — never throws into the caller. */
export function reportError(error: unknown, context?: ErrorContext): void {
  try {
    _provider.captureException(error, context);
  } catch {
    /* swallow: observability must not create errors */
  }
}
