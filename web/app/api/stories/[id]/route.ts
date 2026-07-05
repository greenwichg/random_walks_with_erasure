import { NextResponse } from "next/server";

// A single story's cross-publisher coverage depends on the same story clustering as the Stories
// list, which is not built yet. This endpoint explicitly reports the feature as not yet available
// rather than fabricating coverage; the route is kept as a placeholder for the real implementation.
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json(
    {
      error: {
        code: "not_available",
        message:
          "Story coverage is coming soon — this feature depends on story clustering over the news corpus, which is not built yet.",
      },
    },
    { status: 501 },
  );
}
