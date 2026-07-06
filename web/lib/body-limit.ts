import { NextResponse } from "next/server";

/**
 * Web-tier request-size guard. The engine caps body size centrally, but a route handler that calls
 * `request.json()` buffers the whole body in the Node process *first* — so the public edge needs
 * its own early check to reject a memory-exhaustion payload before parsing. Mirrors the engine's
 * per-class limits and env var names; the body is never read here (Content-Length only) and never
 * logged.
 */

type Scope = "ingest" | "ai" | "write" | "auth" | "default";

const DEFAULTS: Record<Scope, number> = {
  ingest: 1_048_576, // a full batch of reads
  ai: 16_384, // a coach prompt + envelope
  write: 32_768, // settings / onboarding
  auth: 4_096, // tiny identity/token bodies
  default: 16_384,
};

function limitFor(scope: Scope): number {
  const raw = process.env[`RWE_BODY_LIMIT_${scope.toUpperCase()}_BYTES`];
  const n = raw ? Number(raw) : NaN;
  return Number.isFinite(n) && n > 0 ? n : DEFAULTS[scope];
}

/**
 * Returns a typed 413 response when the request's Content-Length exceeds the class limit, else
 * `null` (let the handler proceed). Call at the top of a POST handler:
 *   `const tooLarge = rejectIfTooLarge(request, "ingest"); if (tooLarge) return tooLarge;`
 */
export function rejectIfTooLarge(request: Request, scope: Scope): NextResponse | null {
  const cl = request.headers.get("content-length");
  if (cl && /^\d+$/.test(cl) && Number(cl) > limitFor(scope)) {
    return NextResponse.json(
      { error: { code: "payload_too_large", message: "Request body is too large." } },
      { status: 413, headers: { "Cache-Control": "no-store" } },
    );
  }
  return null;
}
