import { NextResponse } from "next/server";
import type { ReaderGeography } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// Geographic Diversity readiness — the signed-in reader's counted geography (engine
// `/api/me/geography`): countries/languages read + local-vs-national scope, explicit unknowns.
export const dynamic = "force-dynamic";

export async function GET() {
  const data = await backendGet<ReaderGeography>("/api/me/geography", await engineAuthHeaders());
  return data ? NextResponse.json(data) : engineUnavailable();
}
