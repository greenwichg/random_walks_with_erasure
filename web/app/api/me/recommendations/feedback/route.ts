import { NextResponse } from "next/server";
import { backendGet, backendPost, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/** The four canonical feedback signals the engine records (mirrors the backend Literal). */
const FEEDBACK_TYPES = ["like", "dislike", "ignore", "read_later"] as const;

/** Typed 401 matching the engine's error envelope (web/lib/backend.ts shape). */
function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in to record recommendation feedback." } },
    { status: 401 },
  );
}

/**
 * Record the signed-in reader's explicit feedback on a recommendation (like / dislike / ignore /
 * read_later), forwarded to the engine's `/api/me/recommendations/feedback`. The recommendation was
 * already produced by the engine; this only records the reader's signal — **nothing consumes it**
 * (no ranking, personalization, or report path). Attributed via the signed-in session, the same
 * trust boundary as `/api/me/recommendations/opened`; real account state, so no mock fallback.
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as { articleId?: string; feedback?: string };
  if (!body.articleId || !FEEDBACK_TYPES.includes(body.feedback as never)) {
    return NextResponse.json(
      { error: { code: "bad_request", message: "articleId and a valid feedback type are required." } },
      { status: 400 },
    );
  }

  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();

  const result = await backendPost(
    "/api/me/recommendations/feedback",
    { articleId: body.articleId, feedback: body.feedback },
    headers,
  );
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}

/**
 * The signed-in reader's recorded feedback (oldest first) — a read-only projection the
 * Recommendations page uses to keep an *ignored* card dismissed across a reload. Auth required
 * (anonymous readers record nothing); no mock fallback.
 */
export async function GET() {
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();

  const items = await backendGet("/api/me/recommendations/feedback", headers);
  return items ? NextResponse.json(items) : engineUnavailable();
}
