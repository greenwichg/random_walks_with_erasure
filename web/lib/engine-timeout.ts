/**
 * The one place a call to the engine gets a deadline.
 *
 * Node's `fetch` has no request timeout of its own — undici's header/body timeouts are minutes — so a
 * server that accepts a connection and then wedges will hold a promise open long past any point the
 * caller could still use the answer. That is worse than a slow call: `lib/engine-identity.ts`
 * coalesces concurrent callers onto one in-flight promise, so a promise that never settles becomes a
 * shared stall, with no backoff recorded because nothing ever failed.
 *
 * This owns the deadline and nothing else. Header forwarding, cache policy and turning a failure into
 * `null` are call-site decisions and stay at the call sites — `lib/backend.ts` forwards the caller's
 * IP for per-IP rate limiting, and `upsertEngineUser` deliberately does not.
 */

const DEFAULT_TIMEOUT_MS = 6000;

/**
 * The engine deadline in milliseconds, from `RWE_BACKEND_TIMEOUT_MS`.
 *
 * Read per call rather than at module load, so a test can set a short deadline and exercise the real
 * abort path. Also rejects a non-positive or unparseable value instead of passing it through: an empty
 * `RWE_BACKEND_TIMEOUT_MS=` parses to `0`, and a zero-millisecond deadline aborts every engine call
 * instantly — a misconfiguration that would look exactly like a total outage.
 */
export function engineTimeoutMs(): number {
  const raw = Number(process.env.RWE_BACKEND_TIMEOUT_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_TIMEOUT_MS;
}

/**
 * The ceiling for an *opportunistic repair* — currently only identity recovery.
 *
 * Recovery runs inside `callbacks.jwt`, which runs inside `getServerSession`, which a server component
 * awaits. So its deadline is time a reader spends looking at nothing. The general deadline is the right
 * length for work the reader actually asked for: a sign-in that takes six seconds is bad, a sign-in
 * that fails because the engine needed five is worse. A repair has no such claim on the request — it
 * can decline and try again in 30 seconds (`BACKOFF_MS`), and the reader loses nothing but the repair
 * they did not know was happening.
 *
 * Clamped, never flat. Lowering `RWE_BACKEND_TIMEOUT_MS` below this ceiling must lower recovery too,
 * or an operator tightening the engine deadline would leave the *repair* path as the slowest thing in
 * the request — the opposite of what they asked for.
 *
 * No environment variable of its own, deliberately. It is a ratio to a knob that already exists rather
 * than an independent policy, and every new variable has to be threaded onto the `web` service in both
 * compose files (and guarded in `deployment-rules.json`) or it silently does nothing — which is exactly
 * how the identity-recovery kill switch shipped inert the first time.
 */
const RECOVERY_TIMEOUT_MS = 2000;

export function recoveryTimeoutMs(): number {
  return Math.min(engineTimeoutMs(), RECOVERY_TIMEOUT_MS);
}

/**
 * `fetch` with a deadline. Rejects at the deadline — or earlier, with the transport's own error — so
 * the caller decides what a failure means. Always clears its timer, so a resolved call leaves no
 * pending handle behind.
 *
 * The deadline does two things, and the second is the one that makes the guarantee unconditional:
 *
 *   1. `controller.abort()` frees the socket, which is what a well-behaved transport needs.
 *   2. The returned promise is raced against the deadline, so **the caller settles whether or not the
 *      transport honours the signal**. `fetch` is specified to reject on abort and undici does, but
 *      the whole point of this helper is that a caller must never be left attached to a promise that
 *      does not settle; making that depend on the transport's cooperation would leave the failure mode
 *      in place for anything that does not cooperate.
 */
export async function fetchWithTimeout(
  input: string,
  init?: RequestInit,
  timeoutMs: number = engineTimeoutMs(),
): Promise<Response> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;

  const deadline = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new DOMException(`engine call exceeded ${timeoutMs}ms`, "TimeoutError"));
    }, timeoutMs);
  });

  const request = fetch(input, { ...init, signal: controller.signal });
  // The race may settle on the deadline first, leaving this rejection unobserved; swallow it here so
  // it cannot surface as an unhandled rejection. The race still sees the original promise.
  void request.catch(() => {});

  try {
    return await Promise.race([request, deadline]);
  } finally {
    clearTimeout(timer);
  }
}
