import { NextResponse } from "next/server";
import { backendPost } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

/**
 * Unsubscribe from an emailed digest — the ONE authenticated-by-token, session-free write in the
 * app.
 *
 * It forwards the signed token to the engine and nothing else: no session is read, no user id is
 * accepted from the caller. The token names the account; anything the caller could assert would be
 * a way to unsubscribe someone else.
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as { token?: unknown };
  const token = typeof body.token === "string" ? body.token : "";
  // Internal credentials only — deliberately NOT the reader's session, which does not exist here.
  const result = await backendPost("/api/unsubscribe", { token }, await engineAuthHeaders());
  // Always 200 with a boolean: an endpoint that distinguishes "no such user" from "bad signature"
  // is an endpoint that enumerates users.
  return NextResponse.json(result ?? { ok: true, unsubscribed: false });
}
