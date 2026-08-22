import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { backendPostResult } from "@/lib/backend";
import { bearerFromHeader, type TokenResolution } from "@ih/core/logic/auth-decision";

/** The shared-secret header the engine trusts for internal calls, when configured. */
function internalSecretHeaders(): Record<string, string> {
  const secret = process.env.RWE_INTERNAL_SECRET;
  return secret ? { "X-IH-Auth": secret } : {};
}

/**
 * Server-to-server headers that attribute an engine call to the signed-in user.
 *
 * `X-IH-User-Id` names the stable engine user id (from the session); `X-IH-Auth` is the
 * shared secret the engine trusts, sent only when `RWE_INTERNAL_SECRET` is configured.
 * Returns an empty object when there is no signed-in user, so anonymous calls resolve to
 * the demo reader. Call from a route handler (server) and pass the result to
 * `backendGet` / `backendPost`.
 */
export async function engineAuthHeaders(): Promise<Record<string, string>> {
  const session = await getServerSession(authOptions);
  const headers: Record<string, string> = {};
  if (session?.engineUserId != null) {
    headers["X-IH-User-Id"] = String(session.engineUserId);
    Object.assign(headers, internalSecretHeaders());
  }
  return headers;
}

/**
 * Engine headers that attribute a call to a specific user id (already resolved, e.g. from a
 * per-user API token). Same trust boundary as {@link engineAuthHeaders}, minus the session.
 */
export function engineHeadersForUserId(userId: number): Record<string, string> {
  return { "X-IH-User-Id": String(userId), ...internalSecretHeaders() };
}

/**
 * Exchange a per-user API token (from the browser extension's `Authorization: Bearer`) for its
 * engine user id, via the engine's internal resolver. Keeps the token off the engine's public
 * surface: the web tier is the only caller of `/api/internal/resolve-token`, and it then forwards
 * the read on the existing `/api/me/reads` path with {@link engineHeadersForUserId}. Returns
 * `null` for an unknown/invalid token (or an unreachable engine).
 */
export async function resolveApiToken(token: string): Promise<number | null> {
  const resolved = await resolveApiTokenResult(token);
  return resolved.status === "ok" ? resolved.userId : null;
}

/**
 * The same exchange, keeping the engine's answer intact — the resolver `requireUser` uses.
 *
 * `resolveApiToken`'s `null` cannot distinguish "the engine refused this token" from "the engine did
 * not answer", and the difference is the whole story for a non-browser client: a 401 tells a mobile
 * app its credential is dead and to sign the reader out, while the truth during a deploy is 503,
 * try again. So each engine status is mapped deliberately:
 *
 *   401 / 403   the engine looked and refused → the token is unknown, revoked, or expired
 *   2xx + id    resolved
 *   0           we never reached the engine → no statement about the token exists
 *   anything else (5xx, a 2xx that does not carry a numeric id) is ALSO "unavailable": a broken or
 *   sick engine has not said the token is bad, and inventing that refusal is the failure mode this
 *   function exists to prevent.
 */
export async function resolveApiTokenResult(token: string): Promise<TokenResolution> {
  if (!token) return { status: "rejected" };
  const { status, data } = await backendPostResult<{ userId: number }>(
    "/api/internal/resolve-token",
    { token },
    internalSecretHeaders(),
  );
  if (data && typeof data.userId === "number") return { status: "ok", userId: data.userId };
  if (status === 401 || status === 403) return { status: "rejected" };
  return { status: "unavailable" };
}

/** Extract a bearer token from an `Authorization: Bearer <token>` header, or `null`. */
export function bearerToken(request: Request): string | null {
  return bearerFromHeader(request.headers.get("authorization"));
}
