import { NextResponse } from "next/server";
import { backendGet } from "@/lib/backend";

/**
 * Whether browser push is available, and the VAPID public key to subscribe against.
 *
 * Proxied from the engine rather than baked into the bundle as a `NEXT_PUBLIC_` variable: the switch
 * and the key are then read at call time, so enabling push or rotating the pair is a restart and not
 * a rebuild — the same reasoning that put `RWE_IDENTITY_RECOVERY` on the container rather than in the
 * build (docs/SESSION_IDENTITY_RECOVERY_DESIGN.md §5a).
 *
 * Unauthenticated, like the engine route behind it: a browser must decide whether to offer push
 * before asking the reader for anything, and the only value returned is a public key.
 *
 * Fails CLOSED. An unreachable engine reports push as unavailable, which hides the control — the
 * alternative is offering a reader a permission prompt we cannot honour.
 */
export const dynamic = "force-dynamic";

const UNAVAILABLE = { enabled: false, publicKey: "" };

export async function GET() {
  const config = await backendGet<{ enabled: boolean; publicKey: string }>("/api/push/config");
  return NextResponse.json(config ?? UNAVAILABLE);
}
