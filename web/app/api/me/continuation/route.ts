import { NextResponse } from "next/server";
import type { Continuation } from "@/types/domain";
import { backendGetResult } from "@/lib/backend";
import { optionalUser } from "@/lib/require-user";

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
 *
 * The ONE exception, and it is not a state in between: a caller who presents a bearer token that
 * does not resolve gets a 401 rather than `null`. "No offer" is the right answer for someone who
 * asked anonymously; for a client whose credential was revoked it is a silent lie that would keep
 * the strip blank forever with nothing to diagnose from.
 */
export async function GET(request: Request) {
  const url = new URL(request.url).searchParams.get("url");
  if (!url) return NextResponse.json(null);

  const auth = await optionalUser(request);
  if (!auth.ok) return auth.response;

  const { data } = await backendGetResult<Continuation | null>(
    `/api/me/continuation?url=${encodeURIComponent(url)}`,
    auth.headers,
  );
  return NextResponse.json(data ?? null);
}
