import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { backendPost } from "@/lib/backend";

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
  if (!token) return null;
  const res = await backendPost<{ userId: number }>(
    "/api/internal/resolve-token",
    { token },
    internalSecretHeaders(),
  );
  return res && typeof res.userId === "number" ? res.userId : null;
}

/** Extract a bearer token from an `Authorization: Bearer <token>` header, or `null`. */
export function bearerToken(request: Request): string | null {
  const auth = request.headers.get("authorization") ?? "";
  const token = /^Bearer\s+(.+)$/i.exec(auth.trim())?.[1]?.trim();
  return token ? token : null;
}
