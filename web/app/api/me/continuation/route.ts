import { NextResponse } from "next/server";
import type { Continuation } from "@/types/domain";
import { backendGetResult } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// The reader's live read state decides this, so never a build-time snapshot.
export const dynamic = "force-dynamic";

/**
 * Story Continuation — the post-read "compare this story" offer for one article
 * (docs/STORY_CONTINUATION_DESIGN.md §10.2). Signed-in, read-only, and flag-gated in the engine.
 *
 * **Everything that is not an offer answers `200 null`**, including an unauthenticated caller, an
 * engine outage, and the flag being off. That is deliberate and different from `/api/history`,
 * which surfaces 401 and 503 so the UI can show an explicit error: here the client has exactly two
 * branches — render a strip, or render nothing — and there is no state in between worth a reader's
 * attention. This sits on the Read-click path; a failure must cost them nothing, not produce an
 * error toast about a comparison they never asked for.
 */
export async function GET(request: Request) {
  const url = new URL(request.url).searchParams.get("url");
  if (!url) return NextResponse.json(null);

  const { data } = await backendGetResult<Continuation | null>(
    `/api/me/continuation?url=${encodeURIComponent(url)}`,
    await engineAuthHeaders(),
  );
  return NextResponse.json(data ?? null);
}
