import { NextResponse } from "next/server";

// Stories depend on story clustering over the news corpus, which is not built yet. Rather than
// fabricate clusters, this endpoint explicitly reports the feature as not yet available. The route
// is kept as a placeholder so wiring the real clustering later is a drop-in change.
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json(
    {
      error: {
        code: "not_available",
        message:
          "Stories are coming soon — this feature depends on story clustering over the news corpus, which is not built yet.",
      },
    },
    { status: 501 },
  );
}
