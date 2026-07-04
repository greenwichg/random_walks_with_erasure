import { NextResponse } from "next/server";
import type { EstimateHealthReport } from "@/types/domain";
import { backendPost, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/**
 * Persist the signed-in user's onboarding outlet choices + first estimate. The engine
 * recomputes the estimate from the outlets and stores it. Requires an authenticated session
 * (the auth headers are attached server-side); no mock fallback — this writes real account
 * state, so a down engine surfaces as a 503 rather than a silent success.
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({ outlets: [] }))) as { outlets?: string[] };
  const outlets = Array.isArray(body.outlets) ? body.outlets : [];
  const saved = await backendPost<EstimateHealthReport>(
    "/api/me/onboarding",
    { outlets },
    await engineAuthHeaders(),
  );
  if (saved) return NextResponse.json(saved);
  return engineUnavailable();
}
