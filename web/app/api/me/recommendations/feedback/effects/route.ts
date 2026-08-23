import { NextResponse } from "next/server";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { requireUser } from "@/lib/require-user";

export const dynamic = "force-dynamic";

/**
 * The settings ledger's human-scale view of the reader's recommendation feedback: publisher and
 * topic chips plus the dismissed-article list, grouped ENGINE-side from the same dimensions table
 * the rerank consumes — this proxy adds auth and nothing else, so the display can never hold a
 * grouping opinion of its own.
 */
export async function GET(request: Request) {
  const auth = await requireUser(request, "Sign in to see your recommendation feedback.");
  if (!auth.ok) return auth.response;

  const effects = await backendGet("/api/me/recommendations/feedback/effects", auth.headers);
  return effects ? NextResponse.json(effects) : engineUnavailable();
}
