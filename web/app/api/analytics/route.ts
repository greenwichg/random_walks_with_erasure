import { NextResponse } from "next/server";
import type { AnalyticsSeries } from "@/types/domain";
import { backendGetResult, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { resolveEngineFallback } from "@/lib/engine-fallback";
import { ANALYTICS } from "@/mock/data";

// Reflect the reader's live history at request time, never a build-time snapshot.
export const dynamic = "force-dynamic";

/** Typed 401 for an unauthenticated caller (no signed-in session). */
function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in to view your analytics." } },
    { status: 401 },
  );
}

/**
 * The signed-in reader's analytics from the real engine (`/api/me/analytics`): score / metric /
 * reading / tone / acceptance trends, computed entirely from their stored report snapshots, reads,
 * and recommendation events. A reader with no history yet gets honest empty series.
 *
 * Auth semantics mirror the History proxy (B3): a status-preserving backend call lets an
 * authentication failure (engine 401/403) surface as a 401 — distinct from an engine outage, and
 * never masked by mock data. Only when the engine is genuinely *unavailable* (unreachable, or a 5xx)
 * does development fall back to mock and production return a typed 503.
 */
export async function GET() {
  const result = await backendGetResult<AnalyticsSeries>("/api/me/analytics", await engineAuthHeaders());
  const decision = resolveEngineFallback(result, MOCK_FALLBACK_ENABLED);
  if (decision.kind === "data") return NextResponse.json(decision.data);
  if (decision.kind === "unauthorized") return unauthorized();
  if (decision.kind === "mock") return NextResponse.json(ANALYTICS);
  return engineUnavailable(); // "unavailable" — engine unreachable / 5xx, production
}
