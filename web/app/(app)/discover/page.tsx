"use client";

import { Compass } from "lucide-react";
import { PageContainer } from "@/components/layout/page-container";
import { ComingSoon } from "@/components/shared/coming-soon";

export default function DiscoverPage() {
  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Discover</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">
          Trending stories, clustered across every publisher — coming soon.
        </p>
      </div>

      <ComingSoon
        icon={Compass}
        title="Discover is coming soon"
        description="Discover will surface what's driving the news right now — the same event clustered across left, center, and right publishers, so you can read the whole picture instead of one side of it."
        points={[
          "Depends on story clustering over the news corpus — grouping the many articles that cover a single event.",
          "That clustering isn't built yet, so there are no real trending stories to rank.",
          "Until it's real, we show nothing rather than fabricated stories, source counts, or coverage splits.",
        ]}
      />
    </PageContainer>
  );
}
