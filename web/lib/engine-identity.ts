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
import { fetchWithTimeout, recoveryTimeoutMs } from "./engine-timeout.ts";

const ENGINE_BASE = process.env.RWE_BACKEND_URL ?? "http://127.0.0.1:8000";

interface EngineIdentityInput {
  provider: string;
  providerAccountId: string;
  email?: string | null;
  displayName?: string | null;
  /**
   * `false` asks the engine to resolve the id **without** writing `email`/`displayName` over an
   * existing user's stored profile. Creation is unaffected — a first sighting is still created with
   * them, because creation is not a refresh.
   *
   * Omitted by both sign-in paths, which want today's refresh: their profile comes from a fresh OAuth
   * response. Sent as `false` by recovery, whose profile comes from a session token that may be weeks
   * old and must not overwrite what a newer sign-in already stored.
   */
  refreshProfile?: boolean;
}

/**
 * Why an upsert did not yield an id. Every failure resolves to `null` for the caller, but the *reason*
 * is what an operator needs: `http_401` means the shared secret is wrong, `timeout` means the engine is
 * wedged, `unreachable` means it is down, `malformed_response` means the contract broke. Collapsing
 * them all to `null` before anything can log them is what made a broken recovery indistinguishable from
 * no broken sessions at all.
 */
type UpsertOutcome =
  | { ok: true; userId: number }
  | { ok: false; reason: string; detail?: string };

/**
 * The upsert, with its failure reason intact. `upsertEngineUser` is the thin `number | null` wrapper
 * over this and is what the sign-in paths use; recovery uses this one so it can say what went wrong.
 */
async function attemptEngineUpsert(
  input: EngineIdentityInput,
  timeoutMs?: number,
): Promise<UpsertOutcome> {
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
        // `undefined` is DROPPED by JSON.stringify, so a caller that does not set this sends a body
        // byte-identical to the one it sent before this field existed. That is what keeps both sign-in
        // paths untouched on the wire, and what makes an old engine (which ignores unknown fields)
        // and a new one behave the same for them.
        refreshProfile: input.refreshProfile,
      }),
    }, timeoutMs);   // undefined ⇒ fetchWithTimeout's own default, i.e. engineTimeoutMs()
    if (!res.ok) return { ok: false, reason: `http_${res.status}` };
    const data = (await res.json()) as { userId?: number };
    return typeof data.userId === "number"
      ? { ok: true, userId: data.userId }
      : { ok: false, reason: "malformed_response" };
  } catch (err) {
    // engine down, unreachable, or past the deadline — resolve to demo until it recovers
    const name = err instanceof Error ? err.name : "Error";
    if (name === "TimeoutError" || name === "AbortError") return { ok: false, reason: "timeout" };
    // undici reports a dead socket as `TypeError: fetch failed` and puts the useful part on `cause`,
    // so the bare name would say nothing. ECONNREFUSED vs ENOTFOUND is the difference between "the
    // engine is restarting" and "RWE_BACKEND_URL is wrong", which is worth one field.
    const code = (err as { cause?: { code?: unknown } })?.cause?.code;
    return { ok: false, reason: "unreachable", detail: typeof code === "string" ? code : name };
  }
}

/**
 * Map a third-party identity to the stable engine user id, or `null` if the engine
 * is unreachable — in which case the app simply falls back to the demo reader.
 *
 * `opts.timeoutMs` overrides the deadline for this call only; omitting it keeps the shared
 * `RWE_BACKEND_TIMEOUT_MS` default, which is what both sign-in paths want. The override exists because
 * the right deadline depends on who is waiting: see `recoveryTimeoutMs` in lib/engine-timeout.ts.
 */
export async function upsertEngineUser(
  input: EngineIdentityInput,
  opts?: { timeoutMs?: number },
): Promise<number | null> {
  const outcome = await attemptEngineUpsert(input, opts?.timeoutMs);
  return outcome.ok ? outcome.userId : null;
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
 * ONE CALLER, and it must stay that way: `callbacks.jwt` in `lib/auth-callbacks.ts`. An earlier
 * revision of the plan added a second call site in `engineAuthHeaders`; it was deleted once tracing
 * showed that `routes/session.js` builds the session object from `callbacks.jwt`'s RETURN VALUE, so a
 * heal here is already visible to `engineAuthHeaders` in the same request — no cookie involved
 * (SESSION_IDENTITY_RECOVERY_DESIGN.md §2a). A second entry point would resolve an id the first one
 * had, moments earlier, already put on the token it was about to read.
 * ---------------------------------------------------------------------------------------------- */

/**
 * The kill switch. Default on; `RWE_IDENTITY_RECOVERY=0` (or `false`/`no`/`off`) turns recovery off
 * without a rebuild — edit `deploy/.env`, `docker compose up -d web`, done.
 *
 * Read per call rather than at module load, so the restart is what applies it rather than a rebuild,
 * and so a test can flip it. Disabling reproduces today's behaviour exactly: a token that has an id
 * still uses it (that is not recovery), and a token that has none resolves to `null` as it does now.
 */
function recoveryEnabled(): boolean {
  const raw = (process.env.RWE_IDENTITY_RECOVERY ?? "1").trim().toLowerCase();
  return !["0", "false", "no", "off"].includes(raw);
}

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

/**
 * Whether a token already carries a usable engine user id.
 *
 * One predicate, because there were two and they disagreed. The resolver asked
 * `typeof token.engineUserId === "number"` while `callbacks.jwt` asked `token.engineUserId == null`,
 * which part ways on any non-numeric value: the caller would decline to recover a token the resolver
 * would refuse to use, leaving a session permanently un-attributed. Unreachable today — nothing writes
 * a non-number and JSON round-trips preserve the type — but two definitions of "has an id" is one more
 * than the code can keep true.
 *
 * This adopts the resolver's definition, so the loose check is the only thing that changes: a token
 * carrying garbage is now treated as having no id, which is what `sessionCallback` already believes.
 */
export function hasEngineUserId(token: Pick<RecoverableToken, "engineUserId">): boolean {
  return typeof token.engineUserId === "number";
}

function cacheKey(provider: string, providerAccountId: string): string {
  return `${provider}:${providerAccountId}`;
}

/**
 * The recovery log, in one place so the three outcomes cannot drift apart in shape.
 *
 * OBS1-style structured lines, one per *attempt* — never per request. Everything that answers from the
 * memo, the backoff or the in-flight map returns before reaching here, which is what keeps a broken
 * engine from also becoming a log flood.
 *
 *   engine_identity_recovered         a session was repaired. A rising rate means sign-in-time engine
 *                                     unavailability — look at deploys, not at recovery.
 *   engine_identity_recovery_failed   recovery itself is not working. `reason` says which: http_401 is
 *                                     a wrong/missing RWE_INTERNAL_SECRET, timeout is a wedged engine,
 *                                     unreachable is a dead one. Without this line a totally broken
 *                                     recovery looks exactly like having nothing to recover.
 *   engine_identity_recovery_denied   a signed-in reader is no longer on the allowlist, so their
 *                                     session stays un-attributed until someone acts. Carries the
 *                                     email for the same reason `beta_access_denied` does — the
 *                                     operator has to know who to approve.
 */
function logRecovery(line: Record<string, unknown>): void {
  // eslint-disable-next-line no-console
  console.warn(JSON.stringify(line));
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
  if (hasEngineUserId(token)) return token.engineUserId as number;

  // Below this line is recovery proper, and it is all the kill switch has to disable: returning `null`
  // here is precisely what the code did before recovery existed.
  if (!recoveryEnabled()) return null;

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

  const key = cacheKey(provider, providerAccountId);

  const cached = memo.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.userId;

  const pending = inflight.get(key);
  if (pending) return pending;          // another caller is already asking for this exact identity

  // Recovery is the deferred second half of a sign-in, so it re-runs the gate that sign-in ran. A
  // reader removed from the allowlist since must not get an engine account created by a stale session.
  //
  // AFTER the cache lookups, not before, and the denial is REMEMBERED. `isEmailAllowed` does an
  // uncached `readFileSync` of BETA_ALLOWLIST_FILE, and the caller is `callbacks.jwt`, which runs on
  // every session read — so a denial checked first and left unrecorded would mean synchronous file I/O
  // on every server render, always returning null, for the 30-day life of the token. Recorded as a
  // negative entry it costs one read per backoff window instead. What the check exists to prevent —
  // an engine account created for a removed reader — a cached denial prevents just as well.
  // docs/SESSION_IDENTITY_RECOVERY_DESIGN.md §4.
  const email = typeof token.email === "string" ? token.email : null;
  const access = isEmailAllowed(email);
  if (!access.allowed) {
    remember(key, null);
    logRecovery({ event: "engine_identity_recovery_denied", provider, email, reason: access.reason });
    return null;
  }

  const attempt = attemptEngineUpsert(
    {
      provider,
      providerAccountId,
      email,
      displayName: typeof token.name === "string" ? token.name : null,
      // The token's profile is as old as the session — up to 30 days. Sign-in's is freshly minted, so
      // it refreshes; this one must not. Without this, a reader broken on one device and returning
      // after a newer sign-in elsewhere updated their name would have the old one written back.
      refreshProfile: false,
    },
    // The repair's own deadline, shorter than sign-in's. This call is awaited inside
    // `getServerSession`, so every millisecond is a reader waiting on a render for work they did not
    // ask for. Giving up early costs nothing: the failure is remembered and retried after BACKOFF_MS.
    recoveryTimeoutMs(),
  )
    // `attemptEngineUpsert` swallows its own errors, so this is belt and braces — but it is placed
    // BEFORE the handler below rather than after it on purpose. Normalising a throw into an outcome
    // here means there is exactly one place that remembers and logs, so no interleaving can record the
    // result twice or emit two lines for one attempt.
    .catch((): UpsertOutcome => ({ ok: false, reason: "unexpected" }))
    .then((outcome) => {
      const userId = outcome.ok ? outcome.userId : null;
      remember(key, userId);
      logRecovery(
        outcome.ok
          ? { event: "engine_identity_recovered", provider, userId: outcome.userId }
          : {
              event: "engine_identity_recovery_failed",
              provider,
              reason: outcome.reason,
              ...(outcome.detail ? { detail: outcome.detail } : {}),
            },
      );
      return userId;
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
