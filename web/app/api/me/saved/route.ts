import { NextResponse } from "next/server";
import { backendGet, backendPost, backendDelete, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import type { SavedArticle, SaveResult } from "@/types/domain";

export const dynamic = "force-dynamic";

/** Typed 401 for an unauthenticated caller (no signed-in session). */
function unauthorized() {
  return NextResponse.json(
    { error: { code: "unauthorized", message: "Sign in to save articles." } },
    { status: 401 },
  );
}

/** Typed 400 for a missing article id. */
function badRequest() {
  return NextResponse.json(
    { error: { code: "bad_request", message: "articleId is required." } },
    { status: 400 },
  );
}

/** List the signed-in reader's saved articles (newest first). */
export async function GET() {
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();
  const saved = await backendGet<SavedArticle[]>("/api/me/saved", headers);
  if (saved) return NextResponse.json(saved);
  return engineUnavailable();
}

/** Save an article for the signed-in reader. Idempotent — duplicate saves are ignored by the engine. */
export async function POST(request: Request) {
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();
  const body = (await request.json().catch(() => ({}))) as { articleId?: string; article?: unknown };
  if (!body.articleId) return badRequest();
  const result = await backendPost<SaveResult>(
    "/api/me/saved",
    { articleId: body.articleId, article: body.article ?? {} },
    headers,
  );
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}

/** Remove a saved article. `articleId` is an encoded query param (ids are URLs — never a path segment). */
export async function DELETE(request: Request) {
  const headers = await engineAuthHeaders();
  if (!headers["X-IH-User-Id"]) return unauthorized();
  const articleId = new URL(request.url).searchParams.get("articleId");
  if (!articleId) return badRequest();
  const result = await backendDelete<SaveResult>(
    `/api/me/saved?articleId=${encodeURIComponent(articleId)}`,
    headers,
  );
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}
