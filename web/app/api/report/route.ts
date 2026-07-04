import { NextResponse } from "next/server";
import type { HealthReport } from "@/types/domain";
import { backendGet, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { REPORT } from "@/mock/data";

// Always run at request time so we reflect the live engine (not a build-time snapshot).
export const dynamic = "force-dynamic";

/**
 * Information Health Report. Served by the real Python engine
 * (`health_report.compute` / `user_report`) via examples/api_server.py. In
 * development it falls back to the deterministic mock when the engine is
 * offline; in production it returns a 503 rather than fabricated numbers.
 */
export async function GET() {
  const report = await backendGet<HealthReport>("/api/report", await engineAuthHeaders());
  if (report) return NextResponse.json(report);
  if (MOCK_FALLBACK_ENABLED) return NextResponse.json(REPORT);
  return engineUnavailable();
}
