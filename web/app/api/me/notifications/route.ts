import { NextResponse } from "next/server";
import type { NotificationItem } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// Reflect the reader's notifications at request time (the engine materialises them on read).
export const dynamic = "force-dynamic";

/**
 * The signed-in reader's notifications (newest first), forwarded to the existing engine endpoint
 * `/api/me/notifications`. Anonymous / demo sessions have no notifications — return an **empty list**
 * (not a 401) so the header bell simply shows no badge. Real account state; nothing is fabricated
 * (an empty inbox is honest), so there is no mock fallback — an unreachable engine is a typed 503.
 */
export async function GET() {
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return NextResponse.json([]); // no signed-in engine user → empty inbox

  const items = await backendGet<NotificationItem[]>("/api/me/notifications", headers);
  if (items) return NextResponse.json(items);
  return engineUnavailable();
}
