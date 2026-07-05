import { NextResponse } from "next/server";
import { backendPost, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/** Typed 401 matching the engine's error envelope (web/lib/backend.ts shape). */
function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in to record recommendation opens." } },
    { status: 401 },
  );
}

/**
 * Record that the signed-in user opened a recommended article — the reception signal behind
 * Open-Mindedness. The recommendation was already produced by the engine's recommender; this only
 * marks that the reader engaged with it, forwarded to the *existing* engine endpoint
 * `/api/me/recommendations/opened` (no new recommender, no second pathway). Attributed via the
 * signed-in session, the same trust boundary as `/api/me/reads`; no mock fallback (real account
 * state).
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as {
    articleId?: string;
    crossCutting?: boolean;
  };
  if (!body.articleId) {
    return NextResponse.json(
      { error: { code: "bad_request", message: "articleId is required." } },
      { status: 400 },
    );
  }

  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();

  const result = await backendPost(
    "/api/me/recommendations/opened",
    { articleId: body.articleId, crossCutting: body.crossCutting ?? null },
    headers,
  );
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}
