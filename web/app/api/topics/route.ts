import { NextResponse } from "next/server";

// The topic-browse for Discover was fed mock report topics; Discover itself depends on story
// clustering that is not built yet, so this endpoint explicitly reports as not yet available rather
// than returning fabricated topic data. (A reader's real topic distribution is served by the report
// / dashboard, not here.) Kept as a placeholder for the real Discover implementation.
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json(
    {
      error: {
        code: "not_available",
        message:
          "Discover is coming soon — this feature depends on story clustering over the news corpus, which is not built yet.",
      },
    },
    { status: 501 },
  );
}
