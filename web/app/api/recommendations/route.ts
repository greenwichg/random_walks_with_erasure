import { NextResponse } from "next/server";
import type { Recommendation } from "@ih/core/domain/types";
import { backendGet, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { optionalUser } from "@/lib/require-user";
import { RECOMMENDATIONS } from "@ih/core/logic/mock-data";

// Reflect the live recommender at request time, not a build-time snapshot.
export const dynamic = "force-dynamic";

/**
 * Recommendations from the real RWE family via examples/api_server.py — RWE-B
 * (bounded bridging), RWE-D (long-tail discovery), and Adaptive RWE-B. The
 * `strategy` query param maps to the recommender; omit it for a blended feed.
 * Falls back to the deterministic mock when the engine is offline.
 *
 * `optionalUser`, so a bearer-authenticated client gets ITS OWN feed. Before this, a mobile client
 * presenting a perfectly valid token was resolved as anonymous and served the showcase feed —
 * byte-identical to a signed-out request, and wrong in the way that matters most here: every card
 * carries "this offers another political perspective", which is a claim about the reader's existing
 * diet. `api_fastapi.py: _serve` already refuses to serve that feed to a signed-in reader who has
 * read nothing, for exactly this reason; it simply could not tell that a token-bearing caller was
 * signed in.
 *
 * Anonymous callers keep the showcase feed unchanged — that IS the signed-out landing experience,
 * and the engine's own comment says a visitor browsing it is not being told these are theirs.
 */
export async function GET(request: Request) {
  const strategy = new URL(request.url).searchParams.get("strategy");
  const qs = strategy ? `?strategy=${encodeURIComponent(strategy)}` : "";

  // `recs` is null only when the engine is unreachable; an empty array from a
  // live engine is a real answer and is passed through as-is.
  const auth = await optionalUser(request);
  if (!auth.ok) return auth.response;

  const recs = await backendGet<Recommendation[]>(`/api/recommendations${qs}`, auth.headers);
  if (recs) return NextResponse.json(recs);

  if (!MOCK_FALLBACK_ENABLED) return engineUnavailable();
  const fallback = strategy
    ? RECOMMENDATIONS.filter((r) => r.strategy === strategy)
    : RECOMMENDATIONS;
  return NextResponse.json(fallback);
}

// Feedback (like / dislike / ignore / read_later) is no longer echoed here: it is persisted through
// the authenticated `/api/me/recommendations/feedback` route (real account state, no mock). B1.
