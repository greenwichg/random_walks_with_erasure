import { NextResponse } from "next/server";
import type { Story } from "@ih/core/domain/types";
import { backendGet, engineUnavailable } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

// One clustered story's full cross-publisher coverage, from the engine (examples/discover.py).
export const dynamic = "force-dynamic";

export async function GET(_request: Request, { params }: { params: { id: string } }) {
  const story = await backendGet<Story>(
    `/api/stories/${encodeURIComponent(params.id)}`,
    await engineAuthHeaders(),
  );
  return story ? NextResponse.json(story) : engineUnavailable();
}
