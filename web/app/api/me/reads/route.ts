import { NextResponse } from "next/server";
import { backendPost, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/**
 * Record reading events for the signed-in user — the single ingestion API (paste URL now,
 * browser extension + RSS later). Forwards a batch of reads to the engine, which scores and
 * dedups them. Auth required; no mock fallback (this writes real account state).
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({ reads: [] }))) as { reads?: unknown };
  const reads = Array.isArray(body.reads) ? body.reads : [];
  const result = await backendPost("/api/me/reads", { reads }, await engineAuthHeaders());
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}
