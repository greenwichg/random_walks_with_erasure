import { NextResponse } from "next/server";
import type { Story } from "@ih/core/domain/types";
import { backendGetResult, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// One clustered story's full cross-publisher coverage, from the engine (examples/discover.py).
// The engine's 404 (the event dissolved when the catalog window moved past it — where a stale
// breaking-news notification's deep link lands) passes through as a real 404, exactly like the
// publisher route, so the story page can say "not found" instead of blaming the engine: the old
// `backendGet` collapsed 404 and "engine down" into one null, and the resulting 503 kept the
// page in its retry state forever.
export const dynamic = "force-dynamic";

export async function GET(_request: Request, { params }: { params: { id: string } }) {
  const { status, data } = await backendGetResult<Story>(
    `/api/stories/${encodeURIComponent(params.id)}`,
    await engineAuthHeaders(),
  );
  if (data) return NextResponse.json(data);
  if (status === 404) {
    return NextResponse.json(
      { error: { code: "not_found", message: "Story not found." } },
      { status: 404 },
    );
  }
  return engineUnavailable();
}
