import { NextResponse } from "next/server";
import { backendPost, engineUnavailable } from "@/lib/backend";
import { requireUser } from "@/lib/require-user";

export const dynamic = "force-dynamic";

/**
 * Mark one of the signed-in reader's notifications as seen — forwarded to the existing engine
 * endpoint `/api/me/notifications/{id}/seen` (idempotent and user-scoped engine-side). Real account
 * state; no mock fallback.
 */
export async function POST(request: Request, { params }: { params: { id: string } }) {
  const auth = await requireUser(request, "Sign in to update notifications.");
  if (!auth.ok) return auth.response;

  const id = encodeURIComponent(params.id);
  const result = await backendPost(`/api/me/notifications/${id}/seen`, {}, auth.headers);
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}
