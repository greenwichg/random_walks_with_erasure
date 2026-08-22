import { NextResponse } from "next/server";
import type { NotificationItem } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { optionalUser } from "@/lib/require-user";

// Reflect the reader's notifications at request time (the engine materialises them on read).
export const dynamic = "force-dynamic";

/**
 * The signed-in reader's notifications (newest first), forwarded to the existing engine endpoint
 * `/api/me/notifications`. Anonymous / demo sessions have no notifications — return an **empty list**
 * (not a 401) so the header bell simply shows no badge. Real account state; nothing is fabricated
 * (an empty inbox is honest), so there is no mock fallback — an unreachable engine is a typed 503.
 *
 * The empty list is for a caller who presented NO credential. A bearer token that does not resolve
 * is refused by `optionalUser` before this point, because "your inbox is empty" is the wrong thing
 * to tell a client whose credential was revoked — it looks like an answer, and the client would
 * believe it.
 */
export async function GET(request: Request) {
  const auth = await optionalUser(request);
  if (!auth.ok) return auth.response;
  if (auth.userId === null) return NextResponse.json([]); // no signed-in engine user → empty inbox

  const items = await backendGet<NotificationItem[]>("/api/me/notifications", auth.headers);
  if (items) return NextResponse.json(items);
  return engineUnavailable();
}
