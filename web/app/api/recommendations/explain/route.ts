import { NextResponse } from "next/server";
import type { RecommendationExplain } from "@ih/core/domain/types";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { optionalUser } from "@/lib/require-user";

export const dynamic = "force-dynamic";

/**
 * Proxy for the engine's internal recommendation-explain endpoint (21a.2) — the evidence behind
 * each card's "Why?" drawer. Server-to-server: the engine stays private, and a signed-in
 * session's headers (user id + internal secret) scope the explanation to the caller's own feed;
 * anonymous (demo) sessions work wherever the engine runs without an internal secret (dev).
 * There is deliberately no mock fallback — an explanation is either real or absent.
 *
 * `optionalUser` for the same reason as the feed itself: an explanation scoped to somebody else's
 * recommendations is worse than no explanation, because it reads like an answer.
 */
export async function GET(request: Request) {
  const auth = await optionalUser(request);
  if (!auth.ok) return auth.response;

  const explain = await backendGet<RecommendationExplain>(
    "/api/internal/recommendations/explain",
    auth.headers,
  );
  if (explain) return NextResponse.json(explain);
  return engineUnavailable();
}
