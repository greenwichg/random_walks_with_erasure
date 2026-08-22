import { NextResponse } from "next/server";
import { backendPost, engineUnavailable } from "@/lib/backend";
import { requireUser } from "@/lib/require-user";

export const dynamic = "force-dynamic";

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

  const auth = await requireUser(request, "Sign in to record recommendation opens.");
  if (!auth.ok) return auth.response;

  const result = await backendPost(
    "/api/me/recommendations/opened",
    { articleId: body.articleId, crossCutting: body.crossCutting ?? null },
    auth.headers,
  );
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}
