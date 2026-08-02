import { NextResponse } from "next/server";
import { clampEvents } from "@/lib/rum";

export const dynamic = "force-dynamic";

/**
 * Sink for the RUM beacon (`components/rum-listener.tsx`). One structured log line per event on the
 * web tier's stdout — `docker logs deploy-web-1 | grep '"event":"rum"'` is the query surface.
 *
 * Deliberately NOT forwarded to the engine, unlike `/api/client-errors`: RUM is high-volume
 * telemetry about how fast the system is, and routing it through the system being measured would
 * add engine load in exactly the windows worth measuring. The web tier's own logs are the store.
 *
 * Trusts nothing: the body is clamped through `clampEvents` (event cap, field whitelist, length
 * caps) before a byte of it reaches a log line. Best-effort like every telemetry sink — it never
 * fails the caller.
 */
export async function POST(request: Request) {
  try {
    const body = (await request.json().catch(() => null)) as
      | { sessionId?: unknown; events?: unknown }
      | null;
    const session = typeof body?.sessionId === "string" ? body.sessionId.slice(0, 16) : "-";
    const events = clampEvents(body?.events);
    for (const e of events) {
      // eslint-disable-next-line no-console
      console.log(JSON.stringify({ event: "rum", session, ...e }));
    }
  } catch {
    /* swallow — telemetry must stay silent */
  }
  return NextResponse.json({ ok: true });
}
