import { NextResponse } from "next/server";
import type { PlacePublisher } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// Local News v1 — publishers by locality, from the engine's curated registry
// (examples/api_fastapi.py `/api/places/publishers`). Registry facts only; no catalog scan.
export const dynamic = "force-dynamic";

const FILTER_KEYS = ["country", "region", "city", "scope"] as const;

export async function GET(request: Request) {
  const src = new URL(request.url).searchParams;
  const qs = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = src.get(key);
    if (value) qs.set(key, value);
  }
  const query = qs.toString();
  const data = await backendGet<PlacePublisher[]>(
    `/api/places/publishers${query ? `?${query}` : ""}`,
    await engineAuthHeaders(),
  );
  return data ? NextResponse.json(data) : engineUnavailable();
}
