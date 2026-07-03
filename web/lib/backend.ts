/**
 * Server-side bridge to the Python engine (examples/api_server.py).
 *
 * The Next.js `app/api/*` route handlers call these helpers to reach the real
 * Information Health backend. Everything is behind one env var — set
 * `RWE_BACKEND_URL` to the running engine (default `http://127.0.0.1:8000`).
 *
 * Every call is fail-soft: if the backend is unreachable, slow, or returns a
 * non-200, the helper returns `null` and the caller falls back to mock data.
 * That's what lets us migrate one service at a time without breaking the app
 * when the engine isn't running.
 */

const BASE = process.env.RWE_BACKEND_URL ?? "http://127.0.0.1:8000";
const TIMEOUT_MS = Number(process.env.RWE_BACKEND_TIMEOUT_MS ?? 6000);

async function withTimeout(input: string, init?: RequestInit): Promise<Response | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetch(input, { ...init, signal: controller.signal, cache: "no-store" });
  } catch {
    return null; // ECONNREFUSED, abort/timeout, DNS, …
  } finally {
    clearTimeout(timer);
  }
}

/** GET `<BASE><path>` → parsed JSON, or `null` on any failure (caller falls back). */
export async function backendGet<T>(path: string): Promise<T | null> {
  const res = await withTimeout(`${BASE}${path}`);
  if (!res || !res.ok) return null;
  try {
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** POST `<BASE><path>` with a JSON body → parsed JSON, or `null` on any failure. */
export async function backendPost<T>(path: string, body: unknown): Promise<T | null> {
  const res = await withTimeout(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res || !res.ok) return null;
  try {
    return (await res.json()) as T;
  } catch {
    return null;
  }
}
