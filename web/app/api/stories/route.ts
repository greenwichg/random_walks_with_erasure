import { NextResponse } from "next/server";
import type { StoriesResponse } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { STORY_WIRE_KEYS } from "@/lib/story-wire-keys";

// News events clustered from the live FeedArticle catalog by the engine's Story Service
// (examples/story_service.py). Deterministic clustering — no LLM, no fabrication. Passes the
// filter/sort/pagination params straight through; returns the paginated Story envelope.
// The forwarded-key list lives in lib/story-wire-keys.ts and is RATCHETED by
// request-params.test.ts — a StoryQuery field missing there fails the suite.
export const dynamic = "force-dynamic";

const KEYS = STORY_WIRE_KEYS;

export async function GET(request: Request) {
  const src = new URL(request.url).searchParams;
  const qs = new URLSearchParams();
  for (const key of KEYS) {
    const value = src.get(key);
    if (value) qs.set(key, value);
  }
  const query = qs.toString();
  const data = await backendGet<StoriesResponse>(
    `/api/stories${query ? `?${query}` : ""}`,
    await engineAuthHeaders(),
  );
  return data ? NextResponse.json(data) : engineUnavailable();
}
