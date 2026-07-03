import { NextResponse } from "next/server";
import type { Recommendation } from "@/types/domain";
import { backendGet } from "@/lib/backend";
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
  const recs = await backendGet<Recommendation[]>(`/api/recommendations${qs}`);
  if (recs) return NextResponse.json(recs);

  const fallback = strategy
    ? RECOMMENDATIONS.filter((r) => r.strategy === strategy)
    : RECOMMENDATIONS;
  return NextResponse.json(fallback);
}

/** Record feedback (save / ignore / like / …). Mock: echo it back (no persistence yet). */
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  return NextResponse.json({ ok: true, ...body });
}
