import { NextResponse } from "next/server";
import type { CoachMessage } from "@/types/domain";
import { backendGet, backendPost, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { rejectIfTooLarge } from "@/lib/body-limit";
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
  const history = await backendGet<CoachMessage[]>("/api/coach", await engineAuthHeaders());
  if (history) return NextResponse.json(history);
  if (MOCK_FALLBACK_ENABLED) return NextResponse.json([COACH_GREETING]);
  return engineUnavailable();
}

export async function POST(request: Request) {
  const tooLarge = rejectIfTooLarge(request, "ai");
  if (tooLarge) return tooLarge;
  const { message, echo } = (await request.json().catch(() => ({ message: "" }))) as {
    message: string;
    echo?: unknown;
  };
  // Coach v2 (RWE_COACH_V2): pass the client-carried structured echo through verbatim —
  // binding-only state the engine validates itself; forwarded only when it is a plain object,
  // so a v1 client's body reaches the engine exactly as it does today.
  const body: Record<string, unknown> = { message: message ?? "" };
  if (echo && typeof echo === "object" && !Array.isArray(echo)) body.echo = echo;
  const reply = await backendPost<CoachMessage>("/api/coach", body, await engineAuthHeaders());
  if (reply) return NextResponse.json(reply);
  if (MOCK_FALLBACK_ENABLED) return NextResponse.json(coachReply(message ?? ""));
  return engineUnavailable();
}
