import { NextResponse } from "next/server";
import type { DashboardSummary, NotificationItem, Settings } from "@/types/domain";
import { backendGet } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

/**
 * R1b — the app shell's data, in ONE browser round trip.
 *
 * R1a made the shell queries parallel; the decomposition probes then put the residual cost in the
 * per-request overhead itself: four proxy invocations, four `getServerSession` decodes, four
 * browser connections' worth of jitter, for four payloads that every authenticated page needs.
 * This route asks the engine for all four IN PARALLEL over the private network (where a hop is
 * ~1 ms), decodes the session ONCE, and hands the client one body to seed its caches from.
 *
 * Each section is exactly what its standalone route returns — `ShellPrefetch` seeds the same
 * react-query keys those routes' hooks read, so the shapes must never drift. Sections are
 * independently nullable and the response is always 200: a section the engine could not answer is
 * `null`, the client seeds what it got, and the missing surface falls back to its own hook's
 * on-demand fetch — the aggregate must never turn one slow section into an all-or-nothing shell.
 *
 * Anonymous callers get `{pushConfig}` only (the other three are per-reader); the client never
 * calls this without a session, but the route stays honest if something else does.
 */
export async function GET() {
  const headers = await engineAuthHeaders();
  const signedIn = Boolean(headers["X-IH-User-Id"]);

  const [dashboard, settings, notifications, pushConfig] = await Promise.all([
    signedIn ? backendGet<DashboardSummary>("/api/dashboard", headers) : Promise.resolve(null),
    signedIn ? backendGet<Settings>("/api/me/settings", headers) : Promise.resolve(null),
    signedIn ? backendGet<NotificationItem[]>("/api/me/notifications", headers) : Promise.resolve(null),
    backendGet<{ enabled: boolean; publicKey: string }>("/api/push/config"),
  ]);

  return NextResponse.json({ dashboard, settings, notifications, pushConfig });
}
