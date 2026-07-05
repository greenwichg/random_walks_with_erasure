"use client";

import Link from "next/link";
import { ArrowLeft, Newspaper } from "lucide-react";
import { PageContainer } from "@/components/layout/page-container";
import { ComingSoon } from "@/components/shared/coming-soon";

export default function StoryDetailPage() {
  return (
    <PageContainer>
      <Link
        href="/stories"
        className="mb-5 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> All stories
      </Link>

      <ComingSoon
        icon={Newspaper}
        title="Story coverage is coming soon"
        description="A story's full cross-publisher coverage — the spectrum of how it's framed, which outlets carried it, and where coverage is thin — will appear here once story clustering is built."
        points={[
          "Depends on the same story-clustering algorithm as the Stories list.",
          "No fabricated coverage, source counts, timelines, or confidence scores are shown.",
        ]}
      />
    </PageContainer>
  );
}
