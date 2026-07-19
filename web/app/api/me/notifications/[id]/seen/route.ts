import { NextResponse } from "next/server";
import { backendPost, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/** Typed 401 matching the engine's error envelope. */
function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in to update notifications." } },
    { status: 401 },
  );
}

/**
 * Mark one of the signed-in reader's notifications as seen — forwarded to the existing engine
 * endpoint `/api/me/notifications/{id}/seen` (idempotent and user-scoped engine-side). Real account
 * state; no mock fallback.
 */
export async function POST(_request: Request, { params }: { params: { id: string } }) {
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();

  const id = encodeURIComponent(params.id);
  const result = await backendPost(`/api/me/notifications/${id}/seen`, {}, headers);
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}
