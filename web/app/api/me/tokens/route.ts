import { NextResponse } from "next/server";
import { backendGet, backendPost, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

export const dynamic = "force-dynamic";

type TokenMeta = { id: number; label: string | null; createdAt: string | null; lastUsedAt: string | null };
type TokenMint = { id: number; token: string; label: string | null; createdAt: string | null };

/** Typed 401 for an unauthenticated caller (no signed-in session). */
function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in to manage extension tokens." } },
    { status: 401 },
  );
}

/** List the signed-in user's API tokens (metadata only — never the plaintext). */
export async function GET() {
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();
  const tokens = await backendGet<TokenMeta[]>("/api/me/tokens", headers);
  if (tokens) return NextResponse.json(tokens);
  return engineUnavailable();
}

/**
 * Mint a per-user API token for the browser extension. The engine returns the plaintext once;
 * we pass it straight through so the UI can show it a single time. Auth required (session).
 */
export async function POST(request: Request) {
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();
  const body = (await request.json().catch(() => ({}))) as { label?: string };
  const label = typeof body.label === "string" && body.label.trim() ? body.label.trim() : undefined;
  const minted = await backendPost<TokenMint>("/api/me/tokens", { label }, headers);
  if (minted) return NextResponse.json(minted);
  return engineUnavailable();
}
