/**
 * The one authentication check every `/api/me/*` route runs.
 *
 * `lib/auth-decision.ts` decides *who* a request is; this file supplies the real session, the real
 * token resolver, and turns a refusal into an HTTP response. Two entry points, and the choice
 * between them is entirely about what the route already does for a caller with no credentials:
 *
 *   requireUser   the route answers 401 today  → keep answering 401
 *   optionalUser  the route serves anonymous callers today (`200 []`, `200 null`, or an engine 401
 *                 surfacing as 503) → keep serving them, byte for byte
 *
 * Both run the SAME decision, so the security-critical case cannot be got wrong by picking the
 * wrong one: a bearer token that does not resolve is refused on every route, including the ones
 * that welcome anonymous callers. Falling through to the anonymous answer is the bug this shape
 * exists to make unwritable — it would serve demo content to a client presenting a revoked
 * credential, and the client would have no way to tell.
 *
 * Nothing here reads the request body, so a route may call it before or after parsing.
 */
import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";

import { authOptions } from "@/lib/auth";
import {
  decideAuth,
  statusForFailure,
  codeForFailure,
  type AuthPolicy,
  type AuthVia,
} from "@/lib/auth-decision";
import { engineHeadersForUserId, resolveApiTokenResult } from "@/lib/engine-auth";

/** A request with an identity. `headers` goes straight to `backendGet` / `backendPost`. */
export interface Authed {
  ok: true;
  userId: number;
  via: AuthVia;
  headers: Record<string, string>;
}

/** Same, for a route that also serves anonymous callers: `userId` is `null` and `headers` empty. */
export interface MaybeAuthed {
  ok: true;
  userId: number | null;
  via: AuthVia | null;
  headers: Record<string, string>;
}

/** A refusal, already shaped as the response the route should return. */
export interface Denied {
  ok: false;
  response: NextResponse;
}

/** The engine's error envelope — the shape `lib/backend.ts` and every existing 401 already use. */
function errorResponse(status: number, code: string, message: string): NextResponse {
  return NextResponse.json({ error: { code, message } }, { status });
}

/**
 * `engine_unavailable` is worded identically to `backend.ts`'s `engineUnavailable()` on purpose: a
 * reader who cannot be authenticated because the engine is down is looking at the same outage as a
 * reader whose dashboard failed to load, and two different sentences for it would be a lie about
 * there being two different problems.
 */
function unavailable(): NextResponse {
  return errorResponse(
    503,
    "engine_unavailable",
    "The Information Health engine is temporarily unavailable. Please try again shortly.",
  );
}

async function sessionUserId(): Promise<number | null> {
  const session = await getServerSession(authOptions);
  return session?.engineUserId != null ? Number(session.engineUserId) : null;
}

const probes = { sessionUserId, resolveBearer: resolveApiTokenResult };

/**
 * Authenticate a request, refusing a caller with no identity.
 *
 * `message` is the human half of the 401 and stays whatever the route said before — the wording is
 * the only user-visible thing that differs between these routes ("Sign in to save articles." vs
 * "Sign in to manage extension tokens."), and it is worth keeping.
 *
 *   const auth = await requireUser(request, "Sign in to save articles.");
 *   if (!auth.ok) return auth.response;
 *   const saved = await backendGet<SavedArticle[]>("/api/me/saved", auth.headers);
 */
export async function requireUser(
  request: Request,
  message: string,
  policy?: AuthPolicy,
): Promise<Authed | Denied> {
  const outcome = await decideAuth(request.headers.get("authorization"), probes, policy);
  if (outcome.ok) {
    return {
      ok: true,
      userId: outcome.userId,
      via: outcome.via,
      headers: engineHeadersForUserId(outcome.userId),
    };
  }
  if (outcome.reason === "engine-unavailable") return { ok: false, response: unavailable() };
  return {
    ok: false,
    response: errorResponse(
      statusForFailure(outcome.reason),
      codeForFailure(outcome.reason),
      outcome.reason === "bearer-not-accepted"
        ? "This endpoint requires a signed-in session; an API token is not accepted here."
        : message,
    ),
  };
}

/**
 * Authenticate a request that is allowed to be anonymous.
 *
 * Returns empty headers for a caller with no credentials — which is exactly what
 * `engineAuthHeaders()` returned for them before, so the engine sees the identical anonymous call
 * and the route's existing answer (an empty list, a `null` offer, or the engine's own 401 surfacing
 * as a 503) is unchanged. A bearer token that does not resolve is still refused.
 */
export async function optionalUser(
  request: Request,
  policy?: AuthPolicy,
): Promise<MaybeAuthed | Denied> {
  const outcome = await decideAuth(request.headers.get("authorization"), probes, policy);
  if (outcome.ok) {
    return {
      ok: true,
      userId: outcome.userId,
      via: outcome.via,
      headers: engineHeadersForUserId(outcome.userId),
    };
  }
  if (outcome.reason === "anonymous") return { ok: true, userId: null, via: null, headers: {} };
  if (outcome.reason === "engine-unavailable") return { ok: false, response: unavailable() };
  return {
    ok: false,
    response: errorResponse(
      statusForFailure(outcome.reason),
      codeForFailure(outcome.reason),
      outcome.reason === "bearer-not-accepted"
        ? "This endpoint requires a signed-in session; an API token is not accepted here."
        : "The API token presented is not valid.",
    ),
  };
}

/** The policy for the routes that mint and revoke tokens. See {@link AuthPolicy.acceptBearer}. */
export const SESSION_ONLY: AuthPolicy = { acceptBearer: false };
