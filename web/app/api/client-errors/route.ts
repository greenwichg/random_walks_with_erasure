import { NextResponse } from "next/server";
import { backendPost } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/**
 * Sink for the frontend error reporter's beacon (OBS1). Forwards a browser error report to the engine's
 * `/api/client-errors`, where it joins the same structured, request-correlated log stream + exception
 * reporter as a server error. Best-effort by design: it never fails the caller (error reporting must not
 * itself create errors) and needs no user auth — crashes happen for anonymous visitors too.
 */
export async function POST(request: Request) {
  try {
    const body = await request.json().catch(() => ({}));
    await backendPost("/api/client-errors", body, await engineAuthHeaders());
  } catch {
    /* swallow — reporting is best-effort and must stay silent */
  }
  return NextResponse.json({ ok: true });
}
