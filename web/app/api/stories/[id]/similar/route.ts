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

// Query parameters forwarded to the engine. An ALLOWLIST rather than a pass-through of the whole
// query string, so this route cannot be used to smuggle arbitrary parameters at the engine.
//
// It started as `limit` alone, and the two it was missing were the two that mattered: `minScore`
// and `debug` were dropped here, silently, while an operator probed production with them. Seven
// sweeps of `?minScore=…` all returned the same empty result, which read as evidence about the
// DATA and was evidence about this line. Anything the engine accepts and a caller may set has to
// be listed here or it does not exist.
const FORWARDED = ["limit", "minScore", "debug"] as const;

export async function GET(request: Request, { params }: { params: { id: string } }) {
  const incoming = new URL(request.url).searchParams;
  const out = new URLSearchParams();
  for (const key of FORWARDED) {
    const value = incoming.get(key);
    if (value !== null) out.set(key, value);
  }
  const qs = out.size > 0 ? `?${out.toString()}` : "";
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
