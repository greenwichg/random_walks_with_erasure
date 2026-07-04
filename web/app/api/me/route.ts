import { NextResponse } from "next/server";
import type { EstimateHealthReport, MeasuredHealthReport } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

type Me = {
  onboarding: { outlets: string[] } | null;
  report: EstimateHealthReport | MeasuredHealthReport | null;
};

/** The signed-in user's saved onboarding + latest result (or nulls). Auth required. */
export async function GET() {
  const me = await backendGet<Me>("/api/me", await engineAuthHeaders());
  if (me) return NextResponse.json(me);
  return engineUnavailable();
}
