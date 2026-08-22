import { NextResponse } from "next/server";
import { createRemoteJWKSet, jwtVerify } from "jose";

import { backendPost } from "@/lib/backend";
import { isEmailAllowed } from "@/lib/beta-access";
import { engineHeadersForUserId } from "@/lib/engine-auth";
import { upsertEngineUser } from "@/lib/engine-identity";
import { rejectIfTooLarge } from "@/lib/body-limit";
import {
  exchange,
  messageForExchangeFailure,
  statusForExchangeFailure,
  type ExchangeProbes,
  type VerifiedIdentity,
} from "@/lib/mobile-exchange";

export const dynamic = "force-dynamic";

/**
 * `POST /api/auth/mobile` — the native sign-in exchange.
 *
 * The Android and iOS apps sign in with Google natively and receive an **ID token**. They post it
 * here and get back a Hidden View bearer token: the same per-user credential the browser extension
 * holds, which `requireUser` already accepts on every `/api/me/*` route.
 *
 * The decision — provider, audience, verified email, allowlist, order — is
 * `lib/mobile-exchange.ts`, pure and fully unit-tested. This file supplies the four real probes and
 * turns a refusal into a response.
 *
 * **No secret reaches the phone.** The app never sees `GOOGLE_CLIENT_SECRET`, the internal engine
 * secret, or anything else: it sends a token Google minted for it, and gets one Hidden View minted
 * for it. Native OAuth clients have no client secret at all, which is why this shape is the standard
 * one rather than a workaround.
 */

/** Google's public keys, fetched once and cached by `jose` (it honours the JWKS cache headers). */
const GOOGLE_JWKS = createRemoteJWKSet(new URL("https://www.googleapis.com/oauth2/v3/certs"));
const GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"];

/**
 * The client IDs whose tokens this deployment trusts.
 *
 * Three, and they are genuinely different values: Google issues a separate OAuth client per
 * platform, and a native ID token's `aud` is the **native** client id, never the web one. An
 * audience check written against `GOOGLE_CLIENT_ID` alone rejects every real mobile sign-in — and
 * an audience check omitted entirely accepts any Google ID token ever minted, for any app, which
 * would let any developer's app sign its users into Hidden View.
 *
 * Read per request rather than at module load so a deployment can add a platform with a restart.
 */
function trustedAudiences(): string[] {
  return [
    process.env.GOOGLE_IOS_CLIENT_ID,
    process.env.GOOGLE_ANDROID_CLIENT_ID,
    // The web client is included because Expo's `expo-auth-session` proxy flow issues tokens
    // against it during development. Harmless: it is the same Google project, and the token still
    // has to pass signature, issuer, expiry and the allowlist.
    process.env.GOOGLE_CLIENT_ID,
  ]
    .map((v) => (v ?? "").trim())
    .filter(Boolean);
}

/** Verify signature, issuer and expiry against Google's published keys. Claims are trusted after. */
async function verifyGoogleIdToken(idToken: string): Promise<VerifiedIdentity | null> {
  try {
    // No `audience` here: the audience is checked by the pure decision against the configured list,
    // so an untrusted-but-valid token is distinguishable from a forged one in the log.
    const { payload } = await jwtVerify(idToken, GOOGLE_JWKS, { issuer: GOOGLE_ISSUERS });
    const sub = typeof payload.sub === "string" ? payload.sub : "";
    const aud = Array.isArray(payload.aud) ? payload.aud[0] : payload.aud;
    if (!sub || typeof aud !== "string") return null;
    return {
      providerAccountId: sub,
      email: typeof payload.email === "string" ? payload.email : null,
      displayName: typeof payload.name === "string" ? payload.name : null,
      audience: aud,
      emailVerified: payload.email_verified === true,
    };
  } catch {
    // Expired, forged, wrong issuer, unreachable JWKS — all the same to the caller. `jose` throwing
    // is the only signal that matters here, and the reason is logged as `invalid-token`.
    return null;
  }
}

/** OBS1-style structured line, one per attempt. Never contains the ID token or the minted token. */
function logExchange(line: Record<string, unknown>): void {
  // eslint-disable-next-line no-console
  console.warn(JSON.stringify({ event: "mobile_exchange", ...line }));
}

const probes: ExchangeProbes = {
  verify: verifyGoogleIdToken,
  allowed: (email) => isEmailAllowed(email),
  upsert: (identity) =>
    upsertEngineUser({
      provider: "google",
      providerAccountId: identity.providerAccountId,
      email: identity.email,
      displayName: identity.displayName,
    }),
  mint: async (userId, label) => {
    // Straight to the engine's own mint endpoint with server-to-server headers — the same call
    // `/api/me/tokens` makes for the browser, attributed to the user the ID token named. The phone
    // never presents a Hidden View credential to get one, so `/api/me/tokens` stays SESSION_ONLY
    // and a token still cannot extend itself.
    const minted = await backendPost<{ token: string }>(
      "/api/me/tokens",
      { label },
      engineHeadersForUserId(userId),
    );
    return minted && typeof minted.token === "string" ? minted.token : null;
  },
};

export async function POST(request: Request) {
  const tooLarge = rejectIfTooLarge(request, "write");
  if (tooLarge) return tooLarge;

  const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
  const outcome = await exchange(body, probes, { audiences: trustedAudiences() });

  if (!outcome.ok) {
    logExchange({ ok: false, reason: outcome.reason, detail: outcome.detail });
    return NextResponse.json(
      { error: { code: outcome.reason, message: messageForExchangeFailure(outcome.reason) } },
      { status: statusForExchangeFailure(outcome.reason) },
    );
  }

  logExchange({ ok: true, userId: outcome.userId });
  // The plaintext token exists in exactly one response, as it does for the extension. The client
  // puts it straight into the platform keystore; nothing here stores it (only its hash, engine-side).
  return NextResponse.json({ token: outcome.token, userId: outcome.userId, email: outcome.email });
}
