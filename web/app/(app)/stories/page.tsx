"use client";

import { Newspaper } from "lucide-react";
import { PageContainer } from "@/components/layout/page-container";
import { ComingSoon } from "@/components/shared/coming-soon";

export default function StoriesPage() {
  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Stories</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">
          One event, every viewpoint — coming soon.
        </p>
      </div>

      <ComingSoon
        icon={Newspaper}
        title="Stories is coming soon"
        description="Stories will cluster coverage of a single event across publishers, so you can see how the same story is framed — left, center, and right — side by side."
        points={[
          "Depends on story clustering / event grouping over the news corpus.",
          "That algorithm isn't built yet, so there are no real clusters to show.",
          "No fabricated stories, source counts, coverage timelines, or blind-spot claims are shown here.",
        ]}
      />
    </PageContainer>
  );
}
