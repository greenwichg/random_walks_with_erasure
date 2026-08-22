import { NextResponse } from "next/server";
import type { EstimateHealthReport } from "@ih/core/domain/types";
import { backendPost, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { rejectIfTooLarge } from "@/lib/body-limit";
import { mockEstimate } from "@ih/core/logic/mock-onboarding";

// The onboarding estimate is computed live and used before sign-in — public, no caching.
export const dynamic = "force-dynamic";

/**
 * The onboarding Initial Information Health Estimate from selected outlets
 * (POST /api/estimate → engine). Anonymous by design: this runs before an account exists.
 * The response is always flagged mode:"estimate"; falls back to a dev mock when the engine
 * is offline, or a 503 in production (never a fabricated measured report).
 */
export async function POST(request: Request) {
  const tooLarge = rejectIfTooLarge(request, "write");
  if (tooLarge) return tooLarge;
  const body = (await request.json().catch(() => ({ outlets: [] }))) as { outlets?: string[] };
  const outlets = Array.isArray(body.outlets) ? body.outlets : [];
  const estimate = await backendPost<EstimateHealthReport>("/api/estimate", { outlets });
  if (estimate) return NextResponse.json(estimate);
  if (MOCK_FALLBACK_ENABLED) return NextResponse.json(mockEstimate(outlets));
  return engineUnavailable();
}
