import { NextResponse } from "next/server";
import type { Profile } from "@ih/core/domain/types";
import { backendGetResult, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { resolveEngineFallback } from "@ih/core/logic/engine-fallback";
import { PROFILE } from "@ih/core/logic/mock-data";

// Reflect the reader's live account + activity at request time, never a build-time snapshot.
export const dynamic = "force-dynamic";

/** Typed 401 for an unauthenticated caller (no signed-in session). */
function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in to view your profile." } },
    { status: 401 },
  );
}

/**
 * The signed-in reader's real profile from the engine (`/api/me/profile`): identity from their
 * account, streaks from their stored reads, health journey from saved report snapshots. Features
 * that don't exist yet (achievements, saved counts) come back as an honest empty state.
 *
 * Auth semantics mirror the History proxy (B3): a status-preserving backend call lets an
 * authentication failure (engine 401/403) surface as a 401 — distinct from an engine outage, and
 * never masked by mock data. Only when the engine is genuinely *unavailable* (unreachable, or a 5xx)
 * does development fall back to mock and production return a typed 503.
 */
export async function GET() {
  const result = await backendGetResult<Profile>("/api/me/profile", await engineAuthHeaders());
  const decision = resolveEngineFallback(result, MOCK_FALLBACK_ENABLED);
  if (decision.kind === "data") return NextResponse.json(decision.data);
  if (decision.kind === "unauthorized") return unauthorized();
  if (decision.kind === "mock") return NextResponse.json(PROFILE);
  return engineUnavailable(); // "unavailable" — engine unreachable / 5xx, production
}
