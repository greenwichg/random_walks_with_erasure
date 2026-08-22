import { NextResponse } from "next/server";
import { backendDelete, engineUnavailable } from "@/lib/backend";
import { requireUser, SESSION_ONLY } from "@/lib/require-user";

export const dynamic = "force-dynamic";

/**
 * Revoke one of the signed-in user's API tokens (owner-scoped in the engine).
 *
 * `SESSION_ONLY` for the same reason as minting: revocation reached with a bearer token would let a
 * stolen token revoke the reader's *other* tokens — locking the owner out of their own account with
 * the credential they are trying to get rid of.
 */
export async function DELETE(request: Request, { params }: { params: { id: string } }) {
  const auth = await requireUser(request, "Sign in to manage extension tokens.", SESSION_ONLY);
  if (!auth.ok) return auth.response;
  const revoked = await backendDelete<{ ok: boolean }>(`/api/me/tokens/${params.id}`, auth.headers);
  if (revoked) return NextResponse.json(revoked);
  return engineUnavailable();
}
