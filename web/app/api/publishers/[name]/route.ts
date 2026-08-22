import { NextResponse } from "next/server";
import type { PublisherProfile } from "@ih/core/domain/types";
import { backendGetResult, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// Publisher Intelligence profile, from the engine (examples/publisher_service.py). The engine's
// 404 (a name neither the registry nor the catalog knows) passes through as a real 404 so the
// page can say "not found" instead of blaming the engine.
export const dynamic = "force-dynamic";

export async function GET(_request: Request, { params }: { params: { name: string } }) {
  const { status, data } = await backendGetResult<PublisherProfile>(
    `/api/publishers/${encodeURIComponent(params.name)}`,
    await engineAuthHeaders(),
  );
  if (data) return NextResponse.json(data);
  if (status === 404) {
    return NextResponse.json(
      { error: { code: "not_found", message: "Publisher not found." } },
      { status: 404 },
    );
  }
  return engineUnavailable();
}
