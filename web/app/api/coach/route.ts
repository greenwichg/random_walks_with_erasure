import { NextResponse } from "next/server";
import type { CoachMessage } from "@/types/domain";
import { backendGet, backendPost, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { COACH_GREETING, coachReply } from "@/mock/data";

// The coach grounds on the live report, so always run at request time.
export const dynamic = "force-dynamic";

/**
 * AI Coach, backed by narrate_report via examples/api_server.py. Replies are
 * grounded in the reader's real Information Health metrics (report_facts) and
 * carry real RWE-B articles as suggestions. With an LLM key set on the engine
 * the reply is a live narrative; without one it's a deterministic, fully
 * grounded summary. Falls back to the mock coach when the engine is offline.
 */
export async function GET() {
  const history = await backendGet<CoachMessage[]>("/api/coach");
  if (history) return NextResponse.json(history);
  if (MOCK_FALLBACK_ENABLED) return NextResponse.json([COACH_GREETING]);
  return engineUnavailable();
}

export async function POST(request: Request) {
  const { message } = (await request.json().catch(() => ({ message: "" }))) as { message: string };
  const reply = await backendPost<CoachMessage>("/api/coach", { message: message ?? "" });
  if (reply) return NextResponse.json(reply);
  if (MOCK_FALLBACK_ENABLED) return NextResponse.json(coachReply(message ?? ""));
  return engineUnavailable();
}
