import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";

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
    const secret = process.env.RWE_INTERNAL_SECRET;
    if (secret) headers["X-IH-Auth"] = secret;
  }
  return headers;
}
