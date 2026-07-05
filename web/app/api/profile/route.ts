import { NextResponse } from "next/server";
import type { Profile } from "@/types/domain";
import { backendGet, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { PROFILE } from "@/mock/data";

// Reflect the reader's live account + activity at request time, never a build-time snapshot.
export const dynamic = "force-dynamic";

/**
 * The signed-in reader's real profile from the engine (`/api/me/profile`): identity from their
 * account, streaks from their stored reads, health journey from saved report snapshots. Features
 * that don't exist yet (achievements, saved counts) come back as an honest empty state. Falls back
 * to mock only in development when the engine is unreachable; a typed 503 in production.
 */
export async function GET() {
  const profile = await backendGet<Profile>("/api/me/profile", await engineAuthHeaders());
  if (profile) return NextResponse.json(profile);

  if (MOCK_FALLBACK_ENABLED) return NextResponse.json(PROFILE);
  return engineUnavailable();
}
