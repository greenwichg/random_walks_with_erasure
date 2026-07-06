import { NextResponse } from "next/server";
import type { Settings } from "@/types/domain";
import { backendGet, backendPost, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { rejectIfTooLarge } from "@/lib/body-limit";
import { SETTINGS } from "@/mock/data";

// Reflect the reader's saved preferences at request time, never a build-time snapshot.
export const dynamic = "force-dynamic";

/** Typed 401 matching the engine's error envelope. */
function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in to load or save settings." } },
    { status: 401 },
  );
}

/** The signed-in reader's stored preferences (server defaults where unset). Mock only as a dev
 *  fallback when the engine is down; a typed 503 in production. */
export async function GET() {
  const settings = await backendGet<Settings>("/api/me/settings", await engineAuthHeaders());
  if (settings) return NextResponse.json(settings);

  if (MOCK_FALLBACK_ENABLED) return NextResponse.json(SETTINGS);
  return engineUnavailable();
}

/** Persist a (partial) preferences patch for the signed-in reader; returns the full, normalised
 *  settings. Real account state — no mock fallback. */
export async function POST(request: Request) {
  const tooLarge = rejectIfTooLarge(request, "write");
  if (tooLarge) return tooLarge;
  const patch = await request.json().catch(() => ({}));
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();

  const saved = await backendPost<Settings>("/api/me/settings", patch, headers);
  if (saved) return NextResponse.json(saved);
  return engineUnavailable();
}
