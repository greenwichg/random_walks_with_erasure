import { NextResponse } from "next/server";
import type { AnalyticsSeries } from "@/types/domain";
import { backendGet, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { ANALYTICS } from "@/mock/data";

// Reflect the reader's live history at request time, never a build-time snapshot.
export const dynamic = "force-dynamic";

/**
 * The signed-in reader's analytics from the real engine (`/api/me/analytics`): score / metric /
 * reading / tone / acceptance trends, computed entirely from their stored report snapshots, reads,
 * and recommendation events. A reader with no history yet gets honest empty series. Falls back to
 * mock only in development when the engine is unreachable; in production an outage returns a 503.
 */
export async function GET() {
  const analytics = await backendGet<AnalyticsSeries>("/api/me/analytics", await engineAuthHeaders());
  if (analytics) return NextResponse.json(analytics);

  if (MOCK_FALLBACK_ENABLED) return NextResponse.json(ANALYTICS);
  return engineUnavailable();
}
