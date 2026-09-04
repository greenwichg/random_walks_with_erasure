"use client";

import * as React from "react";
import Link from "next/link";
import { Newspaper } from "lucide-react";
import type { Story } from "@ih/core/domain/types";
import type { TopicGroup } from "@ih/core/logic/home";
import { PageContainer } from "@/components/layout/page-container";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { LeadStory } from "@/components/shared/lead-story";
import { SpotCard } from "@/components/shared/spot-card";
import { StoryRow } from "@/components/shared/story-row";
import { FollowButton } from "@/components/shared/follow-button";
import { LocalPulse } from "@/components/home/local-pulse";
import { HomeSkeleton } from "@/components/home/home-skeleton";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { HomeModel } from "@/components/home/home-model";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The home page below `lg` — one column, composed to the mobile reference and built from the
 * SAME shared pieces the desktop front page uses (LeadStory, StoryRow, SpotCard, BiasStrip,
 * FollowButton), so the two layouts are one product at two widths rather than two designs.
 *
 *   Briefing → lens tabs → lead → story rows → More stories
 *   → Blind spots → Daily local news → {Topic} news sections

 * It closes on the news. "Picked for you" and "Your Information Health" used to follow the topic
 * sections; both were about the READER rather than the day, and both have their own destinations
 * in the nav (/recommendations, /report). The desktop front page keeps its own reader module —
 * this is a mobile composition decision, not a product-wide one.
 *
 * The LENS TABS are the reference's feed selector, over the three orderings the page already
 * derives (home-model.ts): most-covered, newest, and the events flagged one-sided. They reorder
 * what is already loaded — no tab costs a request, and none of them is a different feed pretending
 * to be a lens.
 *
 * The topic chip strip is chrome here, not page content: the shell renders it under the masthead
 * on every mobile screen (chrome-slots.tsx), exactly as the reference does.
 */

const SECTION_TITLE = "text-[19px] font-semibold leading-tight tracking-tight";
const LENSES = ["top", "latest", "blindspots"] as const;
type Lens = (typeof LENSES)[number];

export function HomeMobile({
  model,
  loading,
  error,
  onRetry,
}: {
  model: HomeModel;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
  const { t, formatCompact, timeAgo } = useTranslation();
  const { visible, facts, hero, topStories, blindspots, categories, latest } = model;
  const [lens, setLens] = React.useState<Lens>("top");

  const rows = React.useMemo(() => {
    if (lens === "latest") return latest;
    if (lens === "blindspots") return blindspots;
    return topStories;
  }, [lens, topStories, latest, blindspots]);

  return (
    <PageContainer className="pt-4">
      {loading && <HomeSkeleton />}
      {error && <ErrorState onRetry={onRetry} />}
      {!loading && !error && visible.length === 0 && (
        <EmptyState icon={Newspaper} title={t("home.empty.title")} description={t("home.empty.body")} />
      )}

      {visible.length > 0 && (
        <div className="space-y-8">
          {/* Briefing — the day's counted opening statement. */}
          <section aria-labelledby="briefing-heading" className="rounded-md border bg-card p-4">
            <h1 id="briefing-heading" className={cn(SECTION_TITLE, "mb-2")}>
              {t("home.briefing.title")}
            </h1>
            <p className="text-[15px] font-semibold leading-snug tracking-tight">
              {facts.blindspotCount > 0
                ? t("home.briefing.blindspotHeadline", {
                    n: formatCompact(facts.blindspotCount),
                    stories: formatCompact(facts.storyCount),
                  })
                : t("home.briefing.balanced")}
            </p>
            <p className="mt-1.5 text-[12px] text-muted-foreground">
              {t("home.briefing.headline", {
                stories: formatCompact(facts.storyCount),
                publishers: formatCompact(facts.publisherCount),
              })}
            </p>
            <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
              {facts.latestUpdate && <span>{t("home.briefing.updated", { time: timeAgo(facts.latestUpdate) })}</span>}
              <Link href="/analyze" className="font-medium text-foreground/80">
                {t("home.briefing.analyze")}
              </Link>
            </div>
          </section>

          {/* The feed, and the lens over it. */}
          <section aria-labelledby="feed-heading">
            <h2 id="feed-heading" className="sr-only">
              {t("home.newsStories")}
            </h2>
            <Tabs value={lens} onValueChange={(v) => setLens(v as Lens)} className="mb-3">
              <TabsList className="w-full justify-start overflow-x-auto">
                <TabsTrigger value="top">{t("home.lens.top")}</TabsTrigger>
                <TabsTrigger value="latest">{t("home.lens.latest")}</TabsTrigger>
                <TabsTrigger value="blindspots">{t("home.blindspots.title")}</TabsTrigger>
              </TabsList>
            </Tabs>

            {lens === "top" && hero && <LeadStory story={hero} className="mb-2" />}

            {rows.length > 0 ? (
              <ul className={cn(lens === "top" && hero && "border-t")}>
                {rows.map((story: Story) => (
                  <StoryRow key={story.id} story={story} size="lg" thumb action />
                ))}
              </ul>
            ) : (
              <p className="rounded-md border border-dashed bg-card/40 px-4 py-8 text-center text-sm text-muted-foreground">
                {t("home.empty.body")}
              </p>
            )}

            <Button asChild variant="outline" className="mt-5 w-full">
              <Link href={lens === "blindspots" ? "/stories?blindspot=any" : "/stories?sort=latest"}>
                {t("home.moreStories")}
              </Link>
            </Button>
          </section>

          {/* Blind spots — the product's own signal, as picture cards. */}
          {lens === "top" && blindspots.length > 0 && (
            <section aria-labelledby="blindspots-heading">
              <h2 id="blindspots-heading" className={cn(SECTION_TITLE, "mb-1")}>
                {t("home.blindspots.title")}
              </h2>
              <p className="mb-4 text-[12px] leading-relaxed text-muted-foreground">
                {t("home.blindspots.description")}
              </p>
              <ul className="grid grid-cols-2 gap-4">
                {blindspots.slice(0, 2).map((story) => (
                  <SpotCard key={story.id} story={story} />
                ))}
              </ul>
              <Button asChild variant="outline" className="mt-4 w-full">
                <Link href="/stories?blindspot=any">{t("home.blindspots.viewFeed")}</Link>
              </Button>
            </section>
          )}

          <LocalPulse />

          {/* {Topic} news — a lead and its rows, with the topic's own follow control. */}
          {categories.slice(0, 2).map((group: TopicGroup) => (
            <TopicSection key={group.topic} group={group} />
          ))}
        </div>
      )}
    </PageContainer>
  );
}

function TopicSection({ group }: { group: TopicGroup }) {
  const { t } = useTranslation();
  const [lead, ...rest] = group.stories;
  if (!lead) return null;

  return (
    <section aria-labelledby={`topic-${group.topic}`} className="border-t pt-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 id={`topic-${group.topic}`} className="text-[21px] font-bold leading-tight tracking-tight">
          {t("home.topic.section", { topic: group.topic })}
        </h2>
        <FollowButton topic={group.topic} size="button" />
      </div>
      <LeadStory story={lead} size="md" headingLevel="h3" />
      {rest.length > 0 && (
        <ul className="mt-3 border-t">
          {rest.slice(0, 3).map((story) => (
            <StoryRow key={story.id} story={story} size="md" showTopic={false} thumb />
          ))}
        </ul>
      )}
      <Button asChild variant="outline" className="mt-4 w-full">
        <Link href={`/stories?topic=${encodeURIComponent(group.topic)}`}>{t("common.readMore")}</Link>
      </Button>
    </section>
  );
}
