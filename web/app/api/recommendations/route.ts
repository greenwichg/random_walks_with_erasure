import { NextResponse } from "next/server";
import type { Recommendation } from "@/types/domain";
import { backendGet, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { RECOMMENDATIONS } from "@/mock/data";

// Reflect the live recommender at request time, not a build-time snapshot.
export const dynamic = "force-dynamic";

/**
 * Recommendations from the real RWE family via examples/api_server.py — RWE-B
 * (bounded bridging), RWE-D (long-tail discovery), and Adaptive RWE-B. The
 * `strategy` query param maps to the recommender; omit it for a blended feed.
 * Falls back to the deterministic mock when the engine is offline.
 */
export async function GET(request: Request) {
  const strategy = new URL(request.url).searchParams.get("strategy");
  const qs = strategy ? `?strategy=${encodeURIComponent(strategy)}` : "";

  // `recs` is null only when the engine is unreachable; an empty array from a
  // live engine is a real answer and is passed through as-is.
  const recs = await backendGet<Recommendation[]>(`/api/recommendations${qs}`, await engineAuthHeaders());
  if (recs) return NextResponse.json(recs);

  if (!MOCK_FALLBACK_ENABLED) return engineUnavailable();
  const fallback = strategy
    ? RECOMMENDATIONS.filter((r) => r.strategy === strategy)
    : RECOMMENDATIONS;
  return NextResponse.json(fallback);
}

// Feedback (like / dislike / ignore / read_later) is no longer echoed here: it is persisted through
// the authenticated `/api/me/recommendations/feedback` route (real account state, no mock). B1.
