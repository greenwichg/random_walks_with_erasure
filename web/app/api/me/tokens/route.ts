import { NextResponse } from "next/server";
import { backendGet, backendPost, engineUnavailable } from "@/lib/backend";
import { requireUser, SESSION_ONLY } from "@/lib/require-user";

export const dynamic = "force-dynamic";

type TokenMeta = { id: number; label: string | null; createdAt: string | null; lastUsedAt: string | null };
type TokenMint = { id: number; token: string; label: string | null; createdAt: string | null };

/**
 * `SESSION_ONLY`, and this is the one place in `/api/me/*` where it matters.
 *
 * Every other route accepts a bearer token, because that is the point of Phase 1. These two do not:
 * a token that can mint tokens is a token that outlives its own revocation — revoke the one you
 * stole, and the one it minted still works. Listing is refused for the same reason, since a leaked
 * token would otherwise enumerate the reader's other devices. Managing credentials requires the
 * credential a person holds, not one a program does.
 */
const SIGN_IN = "Sign in to manage extension tokens.";

/** List the signed-in user's API tokens (metadata only — never the plaintext). */
export async function GET(request: Request) {
  const auth = await requireUser(request, SIGN_IN, SESSION_ONLY);
  if (!auth.ok) return auth.response;
  const tokens = await backendGet<TokenMeta[]>("/api/me/tokens", auth.headers);
  if (tokens) return NextResponse.json(tokens);
  return engineUnavailable();
}

/**
 * Mint a per-user API token for the browser extension. The engine returns the plaintext once;
 * we pass it straight through so the UI can show it a single time. Auth required (session).
 */
export async function POST(request: Request) {
  const auth = await requireUser(request, SIGN_IN, SESSION_ONLY);
  if (!auth.ok) return auth.response;
  const body = (await request.json().catch(() => ({}))) as { label?: string };
  const label = typeof body.label === "string" && body.label.trim() ? body.label.trim() : undefined;
  const minted = await backendPost<TokenMint>("/api/me/tokens", { label }, auth.headers);
  if (minted) return NextResponse.json(minted);
  return engineUnavailable();
}
