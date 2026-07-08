import { NextResponse } from "next/server";
import type { StoryIntelligence } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// Story Intelligence for one clustered event (examples/story_intelligence.py). Read-only: freshness,
// lifecycle, momentum, coverage statistics, an expanded timeline, "new since your last visit", and
// informational alerts. The signed-in user's headers are forwarded so newSinceLastVisit is computed
// from their existing browser-extension reads; anonymous requests get an empty baseline.
export const dynamic = "force-dynamic";

export async function GET(_request: Request, { params }: { params: { id: string } }) {
  const intel = await backendGet<StoryIntelligence>(
    `/api/story/${encodeURIComponent(params.id)}/intelligence`,
    await engineAuthHeaders(),
  );
  return intel ? NextResponse.json(intel) : engineUnavailable();
}
