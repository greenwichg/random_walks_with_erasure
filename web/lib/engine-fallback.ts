/**
 * The decision a mock-capable, engine-backed proxy GET makes from a *status-preserving* backend
 * result. It keeps the three failure modes the History proxy separated — and that a proxy with a
 * dev mock (Analytics, Profile) extends — distinct, so a 401 (authentication failure), a 503 (engine
 * unavailable), and a transport failure are NEVER collapsed into one response:
 *
 *   - a 2xx body            -> "data"          (serve it — an empty array/object is a real answer)
 *   - status 401 / 403      -> "unauthorized"  (auth failure — never a mock, never a 503)
 *   - otherwise (status 0 transport failure, or a 5xx) — the engine is effectively unavailable:
 *       - mock enabled (dev)  -> "mock"
 *       - else (production)   -> "unavailable"  (a typed 503)
 *
 * Pure and transport-agnostic — it mirrors `backendGetResult`'s `{ status, data }` shape and the
 * route handler maps the returned decision onto a `NextResponse`. This is the one place the
 * 401-vs-503-vs-mock policy lives, so it is unit-testable without Next.js, auth, or the network.
 */

/** A status-preserving backend result: the HTTP status (0 = transport failure) and the parsed body. */
export interface EngineResult<T> {
  status: number;
  data: T | null;
}

export type FallbackDecision<T> =
  | { kind: "data"; data: T }
  | { kind: "unauthorized" }
  | { kind: "mock" }
  | { kind: "unavailable" };

/**
 * Map a backend result + the mock policy to a proxy decision. `data !== null` (not truthiness) marks
 * a real 2xx body, so an empty array/object still serves. A 401/403 is surfaced as `unauthorized`
 * regardless of the mock policy; any other non-body outcome (unreachable engine or a 5xx) is
 * `mock` in development and `unavailable` in production.
 */
export function resolveEngineFallback<T>(
  result: EngineResult<T>,
  mockEnabled: boolean,
): FallbackDecision<T> {
  if (result.data !== null) return { kind: "data", data: result.data };
  if (result.status === 401 || result.status === 403) return { kind: "unauthorized" };
  return mockEnabled ? { kind: "mock" } : { kind: "unavailable" };
}
