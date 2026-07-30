/**
 * The web tier's mapping from a third-party identity to the stable engine user id.
 *
 * Extracted verbatim from `lib/auth.ts`, which called it from the `jwt` callback on first sign-in and
 * from the dev provider's `authorize()`. Nothing about the behaviour changed in the move; the reason
 * for it is that the identity mapping is about to have a second caller, and a helper both the auth
 * layer and its future caller depend on should not live inside the auth layer.
 *
 * The engine owns the durable side of this contract:
 * `docs/IDENTITY_UPSERT_CONCURRENCY.md` — in particular that the upsert is keyed on
 * `(provider, provider_account_id)` and never on email, and that it is idempotent under concurrency.
 */

const ENGINE_BASE = process.env.RWE_BACKEND_URL ?? "http://127.0.0.1:8000";

/**
 * Map a third-party identity to the stable engine user id, or `null` if the engine
 * is unreachable — in which case the app simply falls back to the demo reader.
 */
export async function upsertEngineUser(input: {
  provider: string;
  providerAccountId: string;
  email?: string | null;
  displayName?: string | null;
}): Promise<number | null> {
  try {
    const res = await fetch(`${ENGINE_BASE}/api/internal/users`, {
      method: "POST",
      // The engine's /api/internal/* surface is fail-closed in production: it trusts a call only when
      // it carries the shared secret as X-IH-Auth (same header engineAuthHeaders sends for /api/me/*).
      // Without it this sign-in upsert 401s in prod, engineUserId never resolves, and every per-user
      // page falls through to a 401. Mirror lib/engine-auth.ts's internalSecretHeaders(): send the
      // secret when configured, omit it in dev (RWE_INTERNAL_SECRET unset) where the engine is open.
      headers: {
        "Content-Type": "application/json",
        ...(process.env.RWE_INTERNAL_SECRET
          ? { "X-IH-Auth": process.env.RWE_INTERNAL_SECRET }
          : {}),
      },
      cache: "no-store",
      body: JSON.stringify({
        provider: input.provider,
        providerAccountId: input.providerAccountId,
        email: input.email ?? undefined,
        displayName: input.displayName ?? undefined,
      }),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { userId?: number };
    return typeof data.userId === "number" ? data.userId : null;
  } catch {
    return null; // engine down at sign-in time — resolve to demo until it recovers
  }
}
