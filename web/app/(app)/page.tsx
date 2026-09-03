"use client";

import * as React from "react";
import { useDashboard, useRecommendations, useStories } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { HomeSkeleton } from "@/components/home/home-skeleton";
import { HomeMobile } from "@/components/home/home-mobile";
import { HomeDesktop } from "@/components/home/desktop/home-desktop";
import { STORY_PAGE_SIZE, useHomeModel } from "@/components/home/home-model";
import { useIsDesktop } from "@/lib/use-is-desktop";

/**
 * The Hidden View home page — a news-intelligence front page.
 *
 * ONE `/api/stories` request drives the lead, the story lists, the topic sections, the topic
 * strip and every rail module (components/home/home-model.ts); the reader's own Information
 * Health and recommendation feed ride alongside it. Three queries in total, all of them
 * endpoints that already existed — no new backend surface, no new data contract.
 *
 * Two compositions, one model. On a desktop viewport (`lg`+) the page renders the front page
 * laid out to the desktop reference — topic strip, three columns, topic sections, closing lists
 * (components/home/desktop/home-desktop.tsx). Below it the page is exactly what it was
 * (components/home/home-mobile.tsx). The viewport is read in JS (lib/use-is-desktop.ts) so only
 * ONE tree mounts; until it is known the page shows the same skeleton it shows while loading.
 */
export default function HomePage() {
  const stories = useStories({ sort: "top", limit: STORY_PAGE_SIZE });
  const dashboard = useDashboard();
  const recommendations = useRecommendations();
  const desktop = useIsDesktop();

  const all = React.useMemo(() => stories.data?.stories ?? [], [stories.data]);
  const model = useHomeModel(all);

  if (desktop === null) {
    return (
      <PageContainer>
        <HomeSkeleton />
      </PageContainer>
    );
  }

  if (desktop) {
    return (
      <HomeDesktop
        model={model}
        dashboard={dashboard.data}
        loading={stories.isLoading}
        error={stories.isError}
        onRetry={() => stories.refetch()}
      />
    );
  }

  return (
    <HomeMobile
      model={model}
      dashboard={dashboard.data}
      recommendations={recommendations.data}
      loading={stories.isLoading}
      error={stories.isError}
      onRetry={() => stories.refetch()}
    />
  );
}
