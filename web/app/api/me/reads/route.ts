import { NextResponse } from "next/server";
import { backendPost, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders, engineHeadersForUserId, resolveApiToken, bearerToken } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/** Typed 401 matching the engine's error envelope (web/lib/backend.ts shape). */
function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in or provide a valid extension token." } },
    { status: 401 },
  );
}

/**
 * Record reading events for a user — the single ingestion API. Two authenticated callers,
 * one pipeline:
 *
 *  1. The web app (signed-in session) — the paste-URL flow; attributed via the session.
 *  2. The browser extension — sends its per-user `Authorization: Bearer` token; we resolve it to
 *     the engine user id through the internal resolver and forward with that id.
 *
 * Either way the read is forwarded to the *existing* backend `/api/me/reads`, which scores and
 * dedups it — the engine stays private and there is no second ingestion path. No mock fallback
 * (this writes real account state).
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({ reads: [] }))) as { reads?: unknown };
  const reads = Array.isArray(body.reads) ? body.reads : [];

  // 1) Signed-in web session.
  const sessionHeaders = await engineAuthHeaders();
  let headers: Record<string, string> | null = sessionHeaders["X-IH-User-Id"] ? sessionHeaders : null;

  // 2) Otherwise a browser-extension bearer token, resolved server-side to a user id.
  if (!headers) {
    const token = bearerToken(request);
    if (!token) return unauthorized();
    const userId = await resolveApiToken(token);
    if (userId == null) return unauthorized();
    headers = engineHeadersForUserId(userId);
  }

  const result = await backendPost("/api/me/reads", { reads }, headers);
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}
