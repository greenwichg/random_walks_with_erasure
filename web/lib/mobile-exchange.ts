/**
 * The mobile sign-in exchange, decided as a pure function.
 *
 * A native client cannot use the web's sign-in: there is no cookie, no redirect, and no NextAuth
 * session. It signs in with Google natively, gets an **ID token**, and trades it here for a Hidden
 * View bearer token — the same per-user credential the browser extension holds, resolved by the same
 * `/api/internal/resolve-token` path `requireUser` already uses.
 *
 * **Why this is a new endpoint rather than opening up `/api/me/tokens`.** That route is
 * `SESSION_ONLY` by deliberate Phase 1 design: a token that can mint tokens outlives its own
 * revocation — revoke the stolen one and the one it minted still works. Relaxing it to unblock
 * mobile would undo a security decision to avoid building the right thing. This endpoint mints from
 * a *Google identity*, never from a Hidden View token, so that property is preserved exactly.
 *
 * **The gate is not skipped.** `signInCallback` runs `isEmailAllowed` before a browser gets a
 * session; this runs the same function on the same allowlist before a phone gets a token. A second
 * sign-in path that quietly bypassed the closed beta would be the whole point of the beta gone.
 *
 * This module is the DECISION — audience, gate, outcome — as a pure function over injected probes,
 * so every branch is testable without Google, an engine, or a network. `app/api/auth/mobile/route.ts`
 * supplies the real JWKS verifier, the real allowlist and the real engine.
 */

/** The identity a verified ID token asserts. Exactly what the upsert and the gate need. */
export interface VerifiedIdentity {
  /** The provider's stable account id — Google's `sub`. Never the email: emails get reused. */
  providerAccountId: string;
  email: string | null;
  displayName: string | null;
  /** The `aud` claim, checked against the audience allowlist before anything else happens. */
  audience: string;
  /** Whether Google says the address is verified. An unverified email must not pass the allowlist. */
  emailVerified: boolean;
}

export type ExchangeFailure =
  | "unsupported-provider"
  | "missing-token"
  | "invalid-token"
  | "untrusted-audience"
  | "unverified-email"
  | "not-allowlisted"
  | "engine-unavailable"
  | "not-configured";

export type ExchangeOutcome =
  | { ok: true; userId: number; token: string; email: string | null }
  | { ok: false; reason: ExchangeFailure; detail?: string };

/** Everything the decision needs from the outside world. */
export interface ExchangeProbes {
  /** Verify the ID token's signature and claims against the provider's keys. `null` if it fails. */
  verify: (idToken: string) => Promise<VerifiedIdentity | null>;
  /** The closed-beta gate — `web/lib/beta-access.ts`'s `isEmailAllowed`. */
  allowed: (email: string | null) => { allowed: boolean; reason: string };
  /** Map the identity to the stable engine user id, creating on first sight. `null` if unreachable. */
  upsert: (identity: VerifiedIdentity) => Promise<number | null>;
  /** Mint a per-user API token for that engine user. `null` if the engine could not. */
  mint: (userId: number, label: string) => Promise<string | null>;
}

export interface ExchangeRequest {
  provider?: unknown;
  idToken?: unknown;
  /** What the reader will see in Settings → tokens. Free text, truncated. */
  label?: unknown;
}

/** Providers this endpoint accepts. Apple joins the list when Guideline 4.8 requires it. */
const PROVIDERS = new Set(["google"]);

/** A device label the reader can recognise in the token list, and cannot use to inject anything. */
export function safeLabel(raw: unknown): string {
  const s = typeof raw === "string" ? raw.trim() : "";
  const cleaned = s.replace(/[^\w .()-]/g, "").slice(0, 40);
  return cleaned || "Mobile app";
}

/**
 * Trade a verified provider identity for a Hidden View token.
 *
 * Order is deliberate and each step is a gate the next one depends on:
 *
 *   1. provider — refuse anything we do not know how to verify, before touching the token
 *   2. verify   — signature, issuer, expiry. Everything after this trusts the claims
 *   3. audience — the token must have been minted FOR one of our clients. A valid Google ID token
 *                 issued to somebody else's app is still a valid Google ID token; without this
 *                 check, any app's token would sign its user into Hidden View
 *   4. verified email — an unverified address must not be matched against the allowlist, or the
 *                 allowlist can be defeated by claiming somebody else's address
 *   5. allowlist — the closed beta, same gate as the web
 *   6. upsert then mint — the account, then the credential. Never the other way round
 */
export async function exchange(
  req: ExchangeRequest,
  probes: ExchangeProbes,
  opts: { audiences: readonly string[] } = { audiences: [] },
): Promise<ExchangeOutcome> {
  const provider = typeof req.provider === "string" ? req.provider.toLowerCase() : "google";
  if (!PROVIDERS.has(provider)) return { ok: false, reason: "unsupported-provider", detail: provider };

  const idToken = typeof req.idToken === "string" ? req.idToken.trim() : "";
  if (!idToken) return { ok: false, reason: "missing-token" };

  // An empty audience list means nobody configured GOOGLE_*_CLIENT_ID. Fail closed and say so:
  // accepting any audience "until it is configured" is how a placeholder becomes production.
  if (opts.audiences.length === 0) return { ok: false, reason: "not-configured" };

  const identity = await probes.verify(idToken);
  if (!identity) return { ok: false, reason: "invalid-token" };

  if (!opts.audiences.includes(identity.audience)) {
    return { ok: false, reason: "untrusted-audience", detail: identity.audience };
  }
  if (!identity.emailVerified) return { ok: false, reason: "unverified-email" };

  const gate = probes.allowed(identity.email);
  if (!gate.allowed) return { ok: false, reason: "not-allowlisted", detail: gate.reason };

  const userId = await probes.upsert(identity);
  if (userId == null) return { ok: false, reason: "engine-unavailable", detail: "upsert" };

  const token = await probes.mint(userId, safeLabel(req.label));
  if (token == null) return { ok: false, reason: "engine-unavailable", detail: "mint" };

  return { ok: true, userId, token, email: identity.email };
}

/**
 * The HTTP status a failure is owed.
 *
 * `not-allowlisted` is 403, not 401: the credential was fine and the person is simply not in the
 * beta. Answering 401 would send a native client into a re-authentication loop against a door that
 * is never going to open for them.
 */
export function statusForExchangeFailure(reason: ExchangeFailure): 400 | 401 | 403 | 500 | 503 {
  switch (reason) {
    case "unsupported-provider":
    case "missing-token":
      return 400;
    case "invalid-token":
    case "untrusted-audience":
    case "unverified-email":
      return 401;
    case "not-allowlisted":
      return 403;
    case "not-configured":
      return 500;
    case "engine-unavailable":
      return 503;
  }
}

/**
 * What the client is told. Deliberately coarser than the internal reason.
 *
 * `untrusted-audience` and `invalid-token` both surface as "the sign-in could not be verified":
 * telling an attacker which of the two failed tells them whether they have guessed a real client id.
 * The precise reason goes to the log, where the operator is.
 */
export function messageForExchangeFailure(reason: ExchangeFailure): string {
  switch (reason) {
    case "unsupported-provider":
      return "That sign-in provider is not supported.";
    case "missing-token":
      return "No sign-in token was supplied.";
    case "invalid-token":
    case "untrusted-audience":
    case "unverified-email":
      return "The sign-in could not be verified. Please try again.";
    case "not-allowlisted":
      return "Hidden View is in a closed beta and this account is not on the list yet.";
    case "not-configured":
      return "Mobile sign-in is not configured on this deployment.";
    case "engine-unavailable":
      return "The Information Health engine is temporarily unavailable. Please try again shortly.";
  }
}
