/**
 * Server-side bridge to the Python engine (examples/api_server.py).
 *
 * The Next.js `app/api/*` route handlers call these helpers to reach the real
 * Information Health backend. Everything is behind one env var — set
 * `RWE_BACKEND_URL` to the running engine (default `http://127.0.0.1:8000`).
 *
 * Each `backendGet`/`backendPost` is fail-soft: on any transport error it
 * returns `null`. Whether a route may then serve mock data is a *policy*
 * decision — see `MOCK_FALLBACK_ENABLED`.
 */

import { NextResponse } from "next/server";
import { headers } from "next/headers";

const BASE = process.env.RWE_BACKEND_URL ?? "http://127.0.0.1:8000";
const TIMEOUT_MS = Number(process.env.RWE_BACKEND_TIMEOUT_MS ?? 6000);

/**
 * Forward the originating client's IP to the engine as `X-Forwarded-For`, so the engine's per-IP
 * rate limiting keys on the real caller rather than the web tier's own socket address. Read from
 * the incoming request via `next/headers`; a no-op outside a request scope or when absent (local
 * dev). Authenticated calls are keyed by user id engine-side, so this only affects the anonymous /
 * extension-token paths.
 */
function forwardedFor(): Record<string, string> {
  try {
    const h = headers();
    const ip = h.get("x-forwarded-for") ?? h.get("x-real-ip");
    return ip ? { "x-forwarded-for": ip } : {};
  } catch {
    return {};
  }
}

/**
 * Whether a route backed by the real engine may fall back to mock data when the
 * engine is unreachable.
 *
 * ON in development so the app runs without the engine; OFF in production so a
 * reader is never shown fabricated health numbers during an outage — they get a
 * proper error state instead. Override explicitly with `RWE_ALLOW_MOCK_FALLBACK`
 * (`"true"` / `"false"`). Mock-only routes (no engine counterpart yet) ignore
 * this and always serve mock until their real service lands.
 */
export const MOCK_FALLBACK_ENABLED =
  process.env.RWE_ALLOW_MOCK_FALLBACK === "true" ||
  (process.env.RWE_ALLOW_MOCK_FALLBACK !== "false" && process.env.NODE_ENV !== "production");

/** Typed 503 for when the engine is down and mock fallback is disabled. */
export function engineUnavailable(): NextResponse {
  return NextResponse.json(
    {
      error: {
        code: "engine_unavailable",
        message: "The Information Health engine is temporarily unavailable. Please try again shortly.",
      },
    },
    { status: 503 },
  );
}

async function withTimeout(input: string, init?: RequestInit): Promise<Response | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  // Caller headers (auth / user id) win over the forwarded IP on any conflict.
  const mergedHeaders: Record<string, string> = {
    ...forwardedFor(),
    ...((init?.headers as Record<string, string> | undefined) ?? {}),
  };
  try {
    return await fetch(input, { ...init, headers: mergedHeaders, signal: controller.signal, cache: "no-store" });
  } catch {
    return null; // ECONNREFUSED, abort/timeout, DNS, …
  } finally {
    clearTimeout(timer);
  }
}

/** GET `<BASE><path>` → parsed JSON, or `null` on any failure (caller falls back).
 *  Optional `headers` attribute the call to a signed-in user (see `engineAuthHeaders`). */
export async function backendGet<T>(path: string, headers?: Record<string, string>): Promise<T | null> {
  const res = await withTimeout(`${BASE}${path}`, headers ? { headers } : undefined);
  if (!res || !res.ok) return null;
  try {
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** GET that preserves the HTTP status, so a caller can distinguish auth failure (401/403) from an
 *  unreachable engine (status 0). `data` is `null` on any non-2xx or parse failure. Used where a
 *  route must surface an explicit error instead of falling back to mock data. */
export async function backendGetResult<T>(
  path: string,
  headers?: Record<string, string>,
): Promise<{ status: number; data: T | null }> {
  const res = await withTimeout(`${BASE}${path}`, headers ? { headers } : undefined);
  if (!res) return { status: 0, data: null };
  if (!res.ok) return { status: res.status, data: null };
  try {
    return { status: res.status, data: (await res.json()) as T };
  } catch {
    return { status: res.status, data: null };
  }
}

/** POST `<BASE><path>` with a JSON body → parsed JSON, or `null` on any failure.
 *  Optional `headers` attribute the call to a signed-in user (see `engineAuthHeaders`). */
export async function backendPost<T>(
  path: string,
  body: unknown,
  headers?: Record<string, string>,
): Promise<T | null> {
  const res = await withTimeout(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(headers ?? {}) },
    body: JSON.stringify(body ?? {}),
  });
  if (!res || !res.ok) return null;
  try {
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** DELETE `<BASE><path>` → parsed JSON, or `null` on any failure (caller maps to a status). */
export async function backendDelete<T>(path: string, headers?: Record<string, string>): Promise<T | null> {
  const res = await withTimeout(`${BASE}${path}`, { method: "DELETE", ...(headers ? { headers } : {}) });
  if (!res || !res.ok) return null;
  try {
    return (await res.json()) as T;
  } catch {
    return null;
  }
}
