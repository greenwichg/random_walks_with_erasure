import { NextResponse } from "next/server";
import type { DashboardSummary } from "@ih/core/domain/types";
import { backendGet, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { DASHBOARD } from "@ih/core/logic/mock-data";

// Reflect the reader's live report + reads at request time, never a build-time snapshot.
export const dynamic = "force-dynamic";

/**
 * The home dashboard summary from the real engine (`/api/dashboard`): the reader's overall score and
 * eight metrics (the same report `/api/report` serves), their saved health trend, and today's
 * reading. Falls back to mock only in development when the engine is unreachable; in production an
 * outage returns a typed 503 rather than fabricated numbers.
 */
export async function GET() {
  const dashboard = await backendGet<DashboardSummary>("/api/dashboard", await engineAuthHeaders());
  if (dashboard) return NextResponse.json(dashboard);

  if (MOCK_FALLBACK_ENABLED) return NextResponse.json(DASHBOARD);
  return engineUnavailable();
}
