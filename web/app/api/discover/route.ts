import { NextResponse } from "next/server";
import type { DiscoverResponse } from "@ih/core/domain/types";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { DISCOVER_WIRE_KEYS } from "@ih/core/logic/discover-params";

// Discover — the latest FeedArticles with topic/publisher/lean/type filters, from the engine
// (examples/discover.py). Passes the filter query params straight through.
// The forwarded-key list lives in logic/discover-params.ts and is RATCHETED by its test against
// Required<DiscoverFilters> — a filter field missing there fails the suite instead of being
// silently dropped on the way to the engine.
export const dynamic = "force-dynamic";

const FILTER_KEYS = DISCOVER_WIRE_KEYS;

export async function GET(request: Request) {
  const src = new URL(request.url).searchParams;
  const qs = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = src.get(key);
    if (value) qs.set(key, value);
  }
  const query = qs.toString();
  const data = await backendGet<DiscoverResponse>(
    `/api/discover${query ? `?${query}` : ""}`,
    await engineAuthHeaders(),
  );
  return data ? NextResponse.json(data) : engineUnavailable();
}
