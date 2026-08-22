import { NextResponse } from "next/server";
import type { RecommendationExplain } from "@ih/core/domain/types";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/**
 * Proxy for the engine's internal recommendation-explain endpoint (21a.2) — the evidence behind
 * each card's "Why?" drawer. Server-to-server: the engine stays private, and a signed-in
 * session's headers (user id + internal secret) scope the explanation to the caller's own feed;
 * anonymous (demo) sessions work wherever the engine runs without an internal secret (dev).
 * There is deliberately no mock fallback — an explanation is either real or absent.
 */
export async function GET() {
  const explain = await backendGet<RecommendationExplain>(
    "/api/internal/recommendations/explain",
    await engineAuthHeaders(),
  );
  if (explain) return NextResponse.json(explain);
  return engineUnavailable();
}
