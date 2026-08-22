import { NextResponse } from "next/server";
import { backendGet, backendPost, backendDelete, engineUnavailable } from "@/lib/backend";
import { requireUser } from "@/lib/require-user";
import type { SavedArticle, SaveResult } from "@/types/domain";

export const dynamic = "force-dynamic";

/** The human half of the refusal; the shape and status come from the shared check. */
const SIGN_IN = "Sign in to save articles.";

/** Typed 400 for a missing article id. */
function badRequest() {
  return NextResponse.json(
    { error: { code: "bad_request", message: "articleId is required." } },
    { status: 400 },
  );
}

/** List the signed-in reader's saved articles (newest first). */
export async function GET(request: Request) {
  const auth = await requireUser(request, SIGN_IN);
  if (!auth.ok) return auth.response;
  const saved = await backendGet<SavedArticle[]>("/api/me/saved", auth.headers);
  if (saved) return NextResponse.json(saved);
  return engineUnavailable();
}

/** Save an article for the signed-in reader. Idempotent — duplicate saves are ignored by the engine. */
export async function POST(request: Request) {
  const auth = await requireUser(request, SIGN_IN);
  if (!auth.ok) return auth.response;
  const body = (await request.json().catch(() => ({}))) as { articleId?: string; article?: unknown };
  if (!body.articleId) return badRequest();
  const result = await backendPost<SaveResult>(
    "/api/me/saved",
    { articleId: body.articleId, article: body.article ?? {} },
    auth.headers,
  );
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}

/** Remove a saved article. `articleId` is an encoded query param (ids are URLs — never a path segment). */
export async function DELETE(request: Request) {
  const auth = await requireUser(request, SIGN_IN);
  if (!auth.ok) return auth.response;
  const articleId = new URL(request.url).searchParams.get("articleId");
  if (!articleId) return badRequest();
  const result = await backendDelete<SaveResult>(
    `/api/me/saved?articleId=${encodeURIComponent(articleId)}`,
    auth.headers,
  );
  if (result) return NextResponse.json(result);
  return engineUnavailable();
}
