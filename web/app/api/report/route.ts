import { NextResponse } from "next/server";
import type { HealthReport } from "@/types/domain";
import { backendGet } from "@/lib/backend";
import { REPORT } from "@/mock/data";

// Always run at request time so we reflect the live engine (not a build-time snapshot).
export const dynamic = "force-dynamic";

/**
 * Information Health Report. Served by the real Python engine
 * (`health_report.compute` / `user_report`) via examples/api_server.py, with
 * the deterministic mock as an automatic fallback when the engine is offline.
 */
export async function GET() {
  const report = await backendGet<HealthReport>("/api/report");
  return NextResponse.json(report ?? REPORT);
}
