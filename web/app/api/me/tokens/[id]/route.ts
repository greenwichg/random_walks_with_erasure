import { NextResponse } from "next/server";
import { backendDelete, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/** Revoke one of the signed-in user's API tokens (owner-scoped in the engine). Auth required. */
export async function DELETE(_request: Request, { params }: { params: { id: string } }) {
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) {
    return NextResponse.json(
      { error: { code: "unauthorized", message: "Sign in to manage extension tokens." } },
      { status: 401 },
    );
  }
  const revoked = await backendDelete<{ ok: boolean }>(`/api/me/tokens/${params.id}`, headers);
  if (revoked) return NextResponse.json(revoked);
  return engineUnavailable();
}
