import { NextResponse } from "next/server";
import type { Outlet } from "@ih/core/domain/types";
import { backendGet, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { MOCK_OUTLETS } from "@ih/core/logic/mock-onboarding";

// Reflect the live corpus at request time (public — used before sign-in during onboarding).
export const dynamic = "force-dynamic";

/**
 * Publishers available for onboarding selection, from the engine (GET /api/outlets).
 * Falls back to a dev mock when the engine is offline; a 503 in production.
 */
export async function GET() {
  const outlets = await backendGet<Outlet[]>("/api/outlets");
  if (outlets) return NextResponse.json(outlets);
  if (MOCK_FALLBACK_ENABLED) return NextResponse.json(MOCK_OUTLETS);
  return engineUnavailable();
}
