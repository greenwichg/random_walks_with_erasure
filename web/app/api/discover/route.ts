import { NextResponse } from "next/server";
import type { DiscoverResponse } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// Discover — the latest FeedArticles with topic/publisher/lean filters, from the engine
// (examples/discover.py). Passes the filter query params straight through.
export const dynamic = "force-dynamic";

const FILTER_KEYS = ["topic", "publisher", "lean", "limit"] as const;

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
