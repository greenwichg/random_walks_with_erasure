import { NextResponse } from "next/server";
import type { AnalysisResult } from "@/types/domain";
import { backendPost, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { rejectIfTooLarge } from "@/lib/body-limit";

// The analysis is computed live per request and used before (or without) sign-in — public, no caching.
export const dynamic = "force-dynamic";

/**
 * Article Analyzer (A2-Web) — forwards a URL (+ optional page metadata) to the engine's
 * `POST /api/analyze`, ANALYSIS CONTRACT v1. Anonymous by design (no identity attached); the
 * engine keys its per-IP rate limit on the forwarded client IP. An analysis is a factual claim
 * about a real article, so — like Discover / Stories — there is **no mock fallback**: an
 * unreachable engine is a typed 503, never a fabricated analysis. An unparseable URL is not an
 * error here; the engine returns 200 with `status: "invalid_url"` and the UI guides the reader.
 */
export async function POST(request: Request) {
  const tooLarge = rejectIfTooLarge(request, "write");
  if (tooLarge) return tooLarge;
  const body = (await request.json().catch(() => ({}))) as {
    url?: string;
    metadata?: Record<string, unknown>;
  };
  const payload = {
    url: typeof body.url === "string" ? body.url : "",
    metadata: body.metadata && typeof body.metadata === "object" ? body.metadata : undefined,
  };
  // Forward the signed-in identity so a measured reader gets the A3 reader-relative sections; an
  // anonymous request sends no user header, so the engine returns the byte-identical A2 analysis.
  const analysis = await backendPost<AnalysisResult>("/api/analyze", payload, await engineAuthHeaders());
  if (analysis) return NextResponse.json(analysis);
  return engineUnavailable();
}
