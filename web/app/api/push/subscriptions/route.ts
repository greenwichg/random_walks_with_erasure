import { NextResponse } from "next/server";
import { backendDelete, backendGet, backendPost, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { rejectIfTooLarge } from "@/lib/body-limit";

/**
 * Push subscription registration for the signed-in reader (B1). A thin, attributed proxy to the
 * engine — no push is sent from here or from anywhere else yet.
 *
 * Real account state, so there is no mock fallback in any method: a device that believes it is
 * registered against a mock is a device the sender will never reach.
 *
 * The service worker also calls POST here (on `pushsubscriptionchange`) with `credentials: "include"`,
 * which is why the route must work from a worker context and not only from a page.
 */
export const dynamic = "force-dynamic";

function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in to manage push notifications." } },
    { status: 401 },
  );
}

/** The reader's registered devices. Never includes their encryption keys — the engine strips them. */
export async function GET() {
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();

  const subs = await backendGet<unknown[]>("/api/me/push/subscriptions", headers);
  if (subs) return NextResponse.json(subs);
  return engineUnavailable();
}

/** Register or refresh this device. Idempotent on the endpoint (the engine reassigns or updates). */
export async function POST(request: Request) {
  const tooLarge = rejectIfTooLarge(request, "write");
  if (tooLarge) return tooLarge;
  const body = await request.json().catch(() => ({}));
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();

  const saved = await backendPost<unknown>("/api/me/push/subscriptions", body, headers);
  if (saved) return NextResponse.json(saved);
  return engineUnavailable();
}

/** Unregister this device. `endpoint` is a query parameter because it is a URL. */
export async function DELETE(request: Request) {
  const endpoint = new URL(request.url).searchParams.get("endpoint");
  if (!endpoint) {
    return NextResponse.json(
      { error: { code: "bad_request", message: "endpoint is required." } },
      { status: 400 },
    );
  }
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();

  const removed = await backendDelete<unknown>(
    `/api/me/push/subscriptions?endpoint=${encodeURIComponent(endpoint)}`,
    headers,
  );
  if (removed) return NextResponse.json(removed);
  return engineUnavailable();
}
