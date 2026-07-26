import { NextResponse } from "next/server";
import type { CountryFacet } from "@/types/domain";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// Countries experience — located-catalog + registry facts per country (engine
// `/api/places/countries`). Counted facts only; registry-only countries carry honest zeros.
export const dynamic = "force-dynamic";

export async function GET() {
  const data = await backendGet<CountryFacet[]>("/api/places/countries", await engineAuthHeaders());
  return data ? NextResponse.json(data) : engineUnavailable();
}
