import { NextResponse } from "next/server";
import { backendPost } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/**
 * Sink for the frontend product-analytics beacon (PA1). Forwards a batch of events to the engine's
 * `/api/events`, attaching the signed-in user's engine id via {@link engineAuthHeaders} so the engine
 * can attribute authenticated events server-side (anonymous batches carry no user header, so they
 * resolve to the anonymous identity — exactly what the pre-activation funnel needs).
 *
 * Best-effort by design: it never fails the caller (measuring the product must not break it) and needs
 * no user auth. The engine validates the taxonomy, caps the batch, and drops unknown events.
 */
export async function POST(request: Request) {
  try {
    const body = await request.json().catch(() => ({}));
    await backendPost("/api/events", body, await engineAuthHeaders());
  } catch {
    /* swallow — analytics is best-effort and must stay silent */
  }
  return NextResponse.json({ ok: true });
}
