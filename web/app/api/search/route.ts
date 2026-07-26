import { NextResponse } from "next/server";
import type { SearchResponse } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// Live search over the FeedArticle catalog, proxied to the engine (examples/search.py). Passes the
// query/filter/sort/pagination params straight through — the engine searches the catalog directly and
// never touches the recommendation engine. Replaces the former in-memory mock.
export const dynamic = "force-dynamic";

const KEYS = [
  "query",
  "publisher",
  "lean",
  "topic",
  "dateFrom",
  "dateTo",
  "source",
  "country",
  "sort",
  "limit",
  "offset",
  "debug",
] as const;

export async function GET(request: Request) {
  const src = new URL(request.url).searchParams;
  const qs = new URLSearchParams();
  for (const key of KEYS) {
    const value = src.get(key);
    if (value) qs.set(key, value);
  }
  const query = qs.toString();
  const data = await backendGet<SearchResponse>(
    `/api/search${query ? `?${query}` : ""}`,
    await engineAuthHeaders(),
  );
  return data ? NextResponse.json(data) : engineUnavailable();
}
