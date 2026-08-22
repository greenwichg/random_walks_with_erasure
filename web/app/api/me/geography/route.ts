import { NextResponse } from "next/server";
import type { ReaderGeography } from "@ih/core/domain/types";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { optionalUser } from "@/lib/require-user";

// Geographic Diversity readiness — the signed-in reader's counted geography (engine
// `/api/me/geography`): countries/languages read + local-vs-national scope, explicit unknowns.
export const dynamic = "force-dynamic";

// `optionalUser` keeps the existing answer for an anonymous caller (the engine refuses the
// unattributed call and that surfaces as the 503 below) while adding the bearer path. See
// `app/api/me/route.ts` for why this route is not gated outright.
export async function GET(request: Request) {
  const auth = await optionalUser(request);
  if (!auth.ok) return auth.response;
  const data = await backendGet<ReaderGeography>("/api/me/geography", auth.headers);
  return data ? NextResponse.json(data) : engineUnavailable();
}
