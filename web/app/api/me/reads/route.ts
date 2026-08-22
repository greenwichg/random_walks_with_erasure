import { NextResponse } from "next/server";
import { backendPost, engineUnavailable } from "@/lib/backend";
import { requireUser } from "@/lib/require-user";
import { rejectIfTooLarge } from "@/lib/body-limit";

export const dynamic = "force-dynamic";

/**
 * Record reading events for a user — the single ingestion API. Two authenticated callers,
 * one pipeline:
 *
 *  1. The web app (signed-in session) — the paste-URL flow; attributed via the session.
 *  2. Non-browser clients — the browser extension today, the mobile apps next — sending a per-user
 *     `Authorization: Bearer` token, resolved server-side to the engine user id.
 *
 * Either way the read is forwarded to the *existing* backend `/api/me/reads`, which scores and
 * dedups it — the engine stays private and there is no second ingestion path. No mock fallback
 * (this writes real account state).
 *
 * The session-then-bearer ladder used to be written out here, and this was the only route that had
 * it. It now lives in `lib/require-user.ts` and every `/api/me/*` route runs it, which is the whole
 * of Phase 1: the behaviour below is unchanged, it just stopped being unique to one file.
 */
export async function POST(request: Request) {
  const tooLarge = rejectIfTooLarge(request, "ingest");
  if (tooLarge) return tooLarge;
  const body = (await request.json().catch(() => ({ reads: [] }))) as { reads?: unknown };
  const reads = Array.isArray(body.reads) ? body.reads : [];

  const auth = await requireUser(request, "Sign in or provide a valid extension token.");
  if (!auth.ok) return auth.response;

  // Stamp the read source by auth path (a client may override — e.g. a future import via session).
  // Metadata only: the engine never branches on it; it just attributes the one shared read pipeline.
  //
  // "extension" for every bearer caller keeps the stamp exactly what it was; the mobile apps will
  // send an explicit `readSource` of their own rather than have this guess for them, because the
  // default names how the request AUTHENTICATED, which stopped being the same thing as which client
  // sent it the moment a second bearer client existed.
  const defaultSource = auth.via === "session" ? "app" : "extension";
  const tagged = reads.map((r) =>
    r && typeof r === "object"
      ? { ...(r as Record<string, unknown>), readSource: (r as { readSource?: string }).readSource ?? defaultSource }
      : r,
  );

  const result = await backendPost("/api/me/reads", { reads: tagged }, auth.headers);
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}
