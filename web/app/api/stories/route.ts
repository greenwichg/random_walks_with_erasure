import { NextResponse } from "next/server";
import type { Story } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// News events clustered from the live FeedArticle catalog by the engine (examples/discover.py).
// Deterministic clustering — no LLM, no fabrication. Reflects the catalog at request time.
export const dynamic = "force-dynamic";

export async function GET() {
  const stories = await backendGet<Story[]>("/api/stories", await engineAuthHeaders());
  return stories ? NextResponse.json(stories) : engineUnavailable();
}
