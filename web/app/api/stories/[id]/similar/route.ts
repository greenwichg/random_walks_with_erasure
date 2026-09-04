import { NextResponse } from "next/server";
import type { SimilarStoriesResponse } from "@ih/core/domain/types";
import { backendGetResult, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// Stories about the same or a closely related EVENT, ranked by the engine
// (examples/story_service.similar_stories). The scoring lives there and not here for two reasons:
// it is the clusterer's own similarity measure, so it should not be reimplemented; and answering
// it in the browser would mean shipping the whole 60-story catalog to the story page — ~200 KB and
// a third of the page's API transfer, which is exactly why that list was removed from it.
//
// The engine's 404 (the event dissolved when the catalog window moved past it) passes through as a
// real 404, like the story route beside it, so the page can distinguish "gone" from "engine down".
export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: { id: string } }) {
  const limit = new URL(request.url).searchParams.get("limit");
  const qs = limit ? `?limit=${encodeURIComponent(limit)}` : "";
  const { status, data } = await backendGetResult<SimilarStoriesResponse>(
    `/api/stories/${encodeURIComponent(params.id)}/similar${qs}`,
    await engineAuthHeaders(),
  );
  if (data) return NextResponse.json(data);
  if (status === 404) {
    return NextResponse.json(
      { error: { code: "not_found", message: "Story not found." } },
      { status: 404 },
    );
  }
  return engineUnavailable();
}
