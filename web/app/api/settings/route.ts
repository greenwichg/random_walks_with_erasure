import { NextResponse } from "next/server";
import type { Settings } from "@ih/core/domain/types";
import { backendGet, backendPost, MOCK_FALLBACK_ENABLED, engineUnavailable } from "@/lib/backend";
import { requireUser } from "@/lib/require-user";
import { rejectIfTooLarge } from "@/lib/body-limit";
import { SETTINGS } from "@ih/core/logic/mock-data";

// Reflect the reader's saved preferences at request time, never a build-time snapshot.
export const dynamic = "force-dynamic";

/**
 * The human half of the refusal; the shape and status come from the shared check.
 *
 * `requireUser`, not `optionalUser`: this route already answered 401 to an anonymous caller, and it
 * keeps doing so. What changes is that a bearer token now works — which is what makes Interest
 * Intensity (`interests`), the For You country (`recommendationCountry`) and Political Openness
 * readable and writable from a native client. They are settings, so a mobile app that could not
 * reach them could not honour any of the three.
 */
const SIGN_IN = "Sign in to load or save settings.";

/** The signed-in reader's stored preferences (server defaults where unset). Auth is required —
 *  parity with POST: no session → 401 (never mock). The mock is only a dev fallback for an
 *  authenticated reader when the engine is unreachable; a typed 503 in production. */
export async function GET(request: Request) {
  const auth = await requireUser(request, SIGN_IN);
  if (!auth.ok) return auth.response;

  const settings = await backendGet<Settings>("/api/me/settings", auth.headers);
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
  const auth = await requireUser(request, SIGN_IN);
  if (!auth.ok) return auth.response;

  const saved = await backendPost<Settings>("/api/me/settings", patch, auth.headers);
  if (saved) return NextResponse.json(saved);
  return engineUnavailable();
}
