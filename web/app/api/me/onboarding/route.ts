import { NextResponse } from "next/server";
import type { EstimateHealthReport } from "@/types/domain";
import { backendPost, engineUnavailable } from "@/lib/backend";
import { optionalUser } from "@/lib/require-user";

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
  // `optionalUser`: an anonymous POST already gets the 503 below (the engine refuses to write for
  // nobody), and `/signin/complete` reads that status. See `app/api/me/route.ts`.
  const auth = await optionalUser(request);
  if (!auth.ok) return auth.response;
  const saved = await backendPost<EstimateHealthReport>(
    "/api/me/onboarding",
    { outlets },
    auth.headers,
  );
  if (saved) return NextResponse.json(saved);
  return engineUnavailable();
}
