/**
 * Who a request is, decided as a pure function.
 *
 * The app has two kinds of authenticated caller and is about to have a third:
 *
 *   1. the web app          — a NextAuth session cookie;
 *   2. the browser extension — `Authorization: Bearer <per-user API token>`;
 *   3. the Android/iOS apps  — the same bearer token, on every route rather than one.
 *
 * Until now (2) existed on exactly ONE route (`/api/me/reads`) and was written inline there. Copying
 * that inline shape to forty routes is how the second caller class becomes a second security model:
 * the interesting cases are the ones nobody copies — a token that was revoked, a token that was never
 * issued, a token presented to a route that must not accept one, and an engine that cannot say which.
 * So the decision lives here, once, as data in and a verdict out.
 *
 * **This module is deliberately dependency-free.** No `next/server`, no `next-auth`, no `lib/backend`
 * — those are CommonJS or request-scoped and cannot be imported from bare `node --test` (the same
 * constraint that split `lib/auth-callbacks.ts` out of `lib/auth.ts`). Keeping the verdict pure is
 * what lets every branch below be tested without a server, a database, or a network. The wiring —
 * real session, real token resolver, real HTTP responses — is `lib/require-user.ts`.
 */

/** Which credential answered for this request. */
export type AuthVia = "session" | "bearer";

/**
 * What the engine says about a presented bearer token.
 *
 * `rejected` and `unavailable` are separated on purpose and must never be merged: `rejected` is a
 * statement about the token, `unavailable` is the absence of any statement at all. A caller that
 * treats the second as the first tells a mobile app its credential died when the truth was a deploy.
 */
export type TokenResolution =
  | { status: "ok"; userId: number }
  | { status: "rejected" }
  | { status: "unavailable" };

/**
 * Why a request has no identity.
 *
 *   anonymous            no credential was presented. Some routes 401 on this; others serve a public
 *                        or demo answer. That choice belongs to the route, not here.
 *   invalid-token        a bearer token WAS presented and the engine refused it — unknown, revoked,
 *                        or (should the token model ever grow one) expired. Never falls through to
 *                        the anonymous answer: presenting a credential that does not work is a failed
 *                        authentication, not the absence of one.
 *   bearer-not-accepted  a bearer token was presented to a route that takes a session only. Its own
 *                        reason rather than `invalid-token`, because the token may be perfectly
 *                        valid — it is the route that declines, and 403 says that where 401 lies.
 *   engine-unavailable   we could not find out. The honest answer is 503.
 */
export type AuthFailure =
  | "anonymous"
  | "invalid-token"
  | "bearer-not-accepted"
  | "engine-unavailable";

export type AuthOutcome =
  | { ok: true; userId: number; via: AuthVia }
  | { ok: false; reason: AuthFailure };

/** The two lookups the decision needs, injected so the decision itself stays pure. */
export interface AuthProbes {
  /** The signed-in engine user id from the session, or `null` when there is no session. */
  sessionUserId: () => Promise<number | null>;
  /** Exchange a bearer token for its engine user id. Only called when a token was presented. */
  resolveBearer: (token: string) => Promise<TokenResolution>;
}

export interface AuthPolicy {
  /**
   * Whether this route accepts a bearer token at all. Default `true`.
   *
   * `false` is for the routes that MINT and REVOKE tokens. A token that can mint tokens is a token
   * that survives its own revocation, so token management stays session-only — the credential can
   * never be used to extend itself.
   */
  acceptBearer?: boolean;
}

/**
 * The token out of an `Authorization: Bearer <token>` header, or `null` when the header is absent,
 * empty, or some other scheme. Case-insensitive on the scheme, per RFC 7235.
 *
 * The single parser in the codebase; `lib/engine-auth.ts`'s `bearerToken(request)` is the Request
 * -shaped shim over it.
 */
export function bearerFromHeader(authorization: string | null | undefined): string | null {
  const token = /^Bearer\s+(.+)$/i.exec((authorization ?? "").trim())?.[1]?.trim();
  return token ? token : null;
}

/**
 * Decide who this request is.
 *
 * **Session first, always.** Not a preference — it is what keeps the web unchanged: a signed-in
 * browser is resolved by exactly the code path that resolved it before this module existed, and the
 * token resolver is never even reached. It also fixes the ordering question a mobile web view raises
 * (a request carrying both a cookie and a token): the session wins, matching what `/api/me/reads`
 * has always done.
 *
 * The token resolver is not called when the route declines bearers. Validity is irrelevant to a
 * route that will not accept one, and asking would stamp `last_used_at` on a token whose request was
 * always going to be refused.
 */
export async function decideAuth(
  authorization: string | null | undefined,
  probes: AuthProbes,
  policy: AuthPolicy = {},
): Promise<AuthOutcome> {
  const sessionUserId = await probes.sessionUserId();
  if (sessionUserId !== null) return { ok: true, userId: sessionUserId, via: "session" };

  const token = bearerFromHeader(authorization);
  if (token === null) return { ok: false, reason: "anonymous" };
  if (policy.acceptBearer === false) return { ok: false, reason: "bearer-not-accepted" };

  const resolved = await probes.resolveBearer(token);
  if (resolved.status === "ok") return { ok: true, userId: resolved.userId, via: "bearer" };
  if (resolved.status === "unavailable") return { ok: false, reason: "engine-unavailable" };
  return { ok: false, reason: "invalid-token" };
}

/** The HTTP status a failure is owed. Kept beside the reasons so the two cannot drift apart. */
export function statusForFailure(reason: AuthFailure): 401 | 403 | 503 {
  if (reason === "bearer-not-accepted") return 403;
  if (reason === "engine-unavailable") return 503;
  return 401;
}

/** The machine-readable `error.code` for a failure, matching the engine's envelope vocabulary. */
export function codeForFailure(reason: AuthFailure): string {
  if (reason === "bearer-not-accepted") return "forbidden";
  if (reason === "engine-unavailable") return "engine_unavailable";
  return "unauthorized";
}
