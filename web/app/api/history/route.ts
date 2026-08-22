import { NextResponse } from "next/server";
import type { HistoryEntry } from "@ih/core/domain/types";
import { backendGetResult, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// Reflect the reader's live reads at request time, never a build-time snapshot.
export const dynamic = "force-dynamic";

/** Typed 401 for an unauthenticated caller (no signed-in session). */
function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in to view your reading history." } },
    { status: 401 },
  );
}

/**
 * The signed-in reader's real reading history — their stored, scored reads from the engine
 * (`/api/me/history`). **Never mock.** An empty history (a brand-new reader) is a real `[]` answer
 * and is passed through; an authentication failure returns 401 and an engine outage returns 503, so
 * the UI shows an explicit error/empty state instead of fabricated demo history. (Previously this
 * silently returned demo data on any failure, which masked the real cause — e.g. a stale extension
 * token or an unauthenticated session — the exact behaviour this route no longer has.)
 */
export async function GET() {
  const { status, data } = await backendGetResult<HistoryEntry[]>(
    "/api/me/history",
    await engineAuthHeaders(),
  );
  if (data) return NextResponse.json(data); // includes [] passthrough (empty history is a real answer)
  if (status === 401 || status === 403) return unauthorized();
  return engineUnavailable();
}
