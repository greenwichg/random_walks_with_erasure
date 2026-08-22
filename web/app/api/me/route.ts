import { NextResponse } from "next/server";
import type { EstimateHealthReport, MeasuredHealthReport } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { optionalUser } from "@/lib/require-user";

export const dynamic = "force-dynamic";

type Me = {
  onboarding: { outlets: string[] } | null;
  report: EstimateHealthReport | MeasuredHealthReport | null;
  // Stored read count. Read by `/signin/complete` (with `onboarding`, via `needsOnboarding`) to tell a
  // never-initialized account from an established one before landing a pre-sign-in selection.
  reads?: number;
};

/**
 * The signed-in user's saved onboarding + latest result (or nulls).
 *
 * `optionalUser`, not `requireUser`, and the distinction is behavioural: an anonymous caller has
 * always reached the engine unattributed, been refused there, and seen the 503 below. Gating here
 * would turn that into a 401 — more accurate, but a different answer than the one `/signin/complete`
 * has been reading since it was written, and this commit changes authentication, not statuses. A
 * bearer token that does not resolve is still refused (see `optionalUser`): the anonymous answer is
 * for callers who presented nothing, never for callers whose credential failed.
 */
export async function GET(request: Request) {
  const auth = await optionalUser(request);
  if (!auth.ok) return auth.response;
  const me = await backendGet<Me>("/api/me", auth.headers);
  if (me) return NextResponse.json(me);
  return engineUnavailable();
}
