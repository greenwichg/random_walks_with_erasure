import { NextResponse } from "next/server";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/**
 * [dev only] Reading-sync identity diagnostics — proxies the engine's `/api/dev/diagnostics`,
 * forwarding the signed-in session identity and an optional `?token=` (the extension's API token).
 * The engine reports the session uid, the uid the token resolves to, whether they match, token
 * validity, and the read count — the one place to see why extension reads aren't showing in Reading
 * History. The engine returns 404 in production, so this surfaces the same "off in prod" behaviour.
 */
export async function GET(request: Request) {
  const token = new URL(request.url).searchParams.get("token");
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  const diag = await backendGet(`/api/dev/diagnostics${qs}`, await engineAuthHeaders());
  if (diag) return NextResponse.json(diag);
  return engineUnavailable();
}
