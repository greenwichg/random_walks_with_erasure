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

// Relative and extension-bearing, unlike most imports in this tree. This module is unit-tested with
// bare `node --test`, whose ESM resolver honours neither the `@/` tsconfig alias nor extensionless
// specifiers. Type-only `@/` imports stay fine anywhere — type stripping erases them before anything
// resolves — but a VALUE import between lib modules has to look like this. `moduleResolution: bundler`
// accepts it, and so do webpack and SWC.
import { isEmailAllowed } from "./beta-access.ts";
import { fetchWithTimeout } from "./engine-timeout.ts";

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
    // fetchWithTimeout, not bare fetch: a wedged engine must fail rather than hang. The recovery path
    // coalesces concurrent callers onto one in-flight promise, so a promise that never settles would
    // stall every one of them and record no backoff (see lib/engine-timeout.ts).
    const res = await fetchWithTimeout(`${ENGINE_BASE}/api/internal/users`, {
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
    return null; // engine down, unreachable, or past the deadline — resolve to demo until it recovers
  }
}

/* ------------------------------------------------------------------------------------------------
 * Identity recovery.
 *
 * A session whose token carries no engine user id cannot be attributed to anyone: every /api/me/*
 * call goes out anonymous and the engine answers 401, for the life of that session. It happens when
 * the engine is unreachable during the few hundred milliseconds of sign-in — a deploy restarting the
 * API is the realistic case — because `callbacks.jwt` resolves the id exactly once and swallows the
 * failure. `resolveEngineUserId` repairs that, from the claims the signed token already carries.
 *
 * Design, including why each cache layer exists and what it may not be relied upon for:
 * docs/SESSION_IDENTITY_RECOVERY_DESIGN.md §3–§5.
 *
 * NOTHING CALLS THIS YET. It is wired to `callbacks.jwt` — the single call site — in commit 5 of
 * docs/IDENTITY_RECOVERY_IMPLEMENTATION_PLAN.md, so that the logic arrives complete and tested before
 * anything depends on it. An earlier revision of that plan added a second call site in
 * `engineAuthHeaders`; it was deleted once tracing showed a heal here is already visible to that
 * function in the same request (SESSION_IDENTITY_RECOVERY_DESIGN.md §2a).
 * ---------------------------------------------------------------------------------------------- */

/** How long a resolved id is reused without asking the engine again. */
const MEMO_TTL_MS = 10 * 60 * 1000;

/** How long a failure suppresses further attempts for that identity. Flat, not exponential: the
 *  outage this recovers from is measured in seconds, and a sick engine should not be retried per
 *  request while it is down. */
const BACKOFF_MS = 30 * 1000;

/** Hard ceiling on distinct identities held at once.
 *
 *  A TTL alone does not bound memory: an entry that is never read again is never evicted, so a
 *  process that sees many identities over its lifetime would grow without limit. Expired entries are
 *  swept on write, and if that is not enough the oldest are dropped — Map preserves insertion order,
 *  so the first key is the oldest. Dropping an entry costs one engine call, never correctness. */
const MAX_ENTRIES = 1_000;

interface CacheEntry {
  /** The resolved id, or `null` for a remembered failure (the backoff entry). */
  userId: number | null;
  expiresAt: number;
}

const memo = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<number | null>>();

/** The claims recovery reads. Every one of them comes from the JWT, which is signed with
 *  `NEXTAUTH_SECRET` — nothing here is taken from a request body, query or header. */
export interface RecoverableToken {
  engineUserId?: unknown;
  provider?: unknown;
  providerAccountId?: unknown;
  sub?: unknown;
  email?: unknown;
  name?: unknown;
}

function cacheKey(provider: string, providerAccountId: string): string {
  return `${provider}:${providerAccountId}`;
}

/** Drop expired entries, then the oldest ones if still over the ceiling. */
function sweep(): void {
  const now = Date.now();
  for (const [key, entry] of memo) {
    if (entry.expiresAt <= now) memo.delete(key);
  }
  while (memo.size > MAX_ENTRIES) {
    const oldest = memo.keys().next();
    if (oldest.done) break;
    memo.delete(oldest.value);
  }
}

function remember(key: string, userId: number | null): void {
  memo.set(key, { userId, expiresAt: Date.now() + (userId === null ? BACKOFF_MS : MEMO_TTL_MS) });
  sweep();
}

/**
 * The engine user id for a session that lost (or never had) one, or `null` if it cannot be resolved.
 *
 * Returns the token's own id untouched when it has one — the overwhelmingly common case, and one
 * `typeof` check with no engine call. Otherwise resolves the identity through the same keyed upsert
 * sign-in uses, so it can only ever return the id that identity already maps to.
 *
 * Never throws: every failure resolves to `null`, which leaves the caller exactly where it is today.
 */
export async function resolveEngineUserId(token: RecoverableToken): Promise<number | null> {
  if (typeof token.engineUserId === "number") return token.engineUserId;

  // A token minted before the provider claims existed carries the Google `sub` in `token.sub`, which
  // IS the provider account id. That fallback is only safe because a `dev` token can never reach this
  // point: the credentials provider fails sign-in outright when its upsert fails, so a dev session
  // always has an id (SESSION_IDENTITY_RECOVERY_DESIGN.md §1).
  const provider = typeof token.provider === "string" ? token.provider : "google";
  if (provider !== "google") return null;

  const providerAccountId =
    typeof token.providerAccountId === "string" && token.providerAccountId
      ? token.providerAccountId
      : typeof token.sub === "string" && token.sub
        ? token.sub
        : null;
  if (!providerAccountId) return null;

  // Recovery is the deferred second half of a sign-in, so it re-runs the gate that sign-in ran. A
  // reader removed from the allowlist since must not get an engine account created by a stale session.
  const email = typeof token.email === "string" ? token.email : null;
  if (!isEmailAllowed(email).allowed) return null;

  const key = cacheKey(provider, providerAccountId);

  const cached = memo.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.userId;

  const pending = inflight.get(key);
  if (pending) return pending;          // another caller is already asking for this exact identity

  const attempt = upsertEngineUser({
    provider,
    providerAccountId,
    email,
    displayName: typeof token.name === "string" ? token.name : null,
  })
    .then((userId) => {
      remember(key, userId);
      if (userId !== null) {
        // OBS1-style line. No email: unlike a beta denial, nobody needs to act on who this was — a
        // rising rate means sign-in-time engine unavailability, which is the thing to look at.
        // eslint-disable-next-line no-console
        console.warn(JSON.stringify({ event: "engine_identity_recovered", provider, userId }));
      }
      return userId;
    })
    .catch(() => {
      remember(key, null);              // upsertEngineUser already swallows; belt and braces
      return null;
    })
    .finally(() => {
      inflight.delete(key);
    });

  inflight.set(key, attempt);
  return attempt;
}

/** Test seam: cache occupancy, for the concurrency and growth tests. Not used in production. */
export function __identityCacheStats(): { entries: number; inflight: number; maxEntries: number } {
  return { entries: memo.size, inflight: inflight.size, maxEntries: MAX_ENTRIES };
}

/** Test seam: forget everything, so one test cannot see another's cache. Not used in production. */
export function __resetIdentityCache(): void {
  memo.clear();
  inflight.clear();
}
