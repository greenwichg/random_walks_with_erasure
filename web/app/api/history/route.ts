import { NextResponse } from "next/server";
import type { HistoryEntry } from "@/types/domain";
import { backendGet, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { HISTORY } from "@/mock/data";

// Reflect the reader's live reads at request time, never a build-time snapshot.
export const dynamic = "force-dynamic";

/**
 * The signed-in reader's real reading history — their stored, scored reads from the engine
 * (`/api/me/history`), rendered as the shared Article shape. An empty array from a live engine is a
 * real answer (a new reader) and is passed through. Falls back to mock data only in development when
 * the engine is unreachable; in production an outage returns a typed 503, never fabricated history.
 */
export async function GET() {
  const history = await backendGet<HistoryEntry[]>("/api/me/history", await engineAuthHeaders());
  if (history) return NextResponse.json(history);

  if (!MOCK_FALLBACK_ENABLED) return engineUnavailable();
  return NextResponse.json(HISTORY);
}
