"use client";

import { briefingFacts, coverageMix, type TopicGroup } from "@/lib/home";
import { SectionHeader } from "@/components/shared/section-header";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { StoryFeatureCard } from "@/components/home/story-feature-card";
import { StoryListItem } from "@/components/home/story-list-item";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * A category module as a MINI EDITORIAL HOMEPAGE — one real catalog topic with its own lead,
 * supporting rows, and a coverage summary, instead of the identical `divide-y` list every topic
 * used to get.
 *
 *   Section header (topic + View all)
 *     → Featured story  (image-forward `StoryFeatureCard`)
 *     → Supporting rows (compact `StoryListItem`s — no synopsis, no labelled split, no images,
 *       so the featured story stays the unambiguous lead)
 *     → Coverage summary (the topic's own aggregate L/C/R mix + counted facts)
 *
 * Personality without fragmentation: consecutive modules alternate which side the featured story
 * sits on (`flip`), which gives a scroll of categories a magazine rhythm while every module reuses
 * the same three components. The topic itself still comes from the corpus (`groupByTopic`) — never
 * a hardcoded desk list.
 *
 * The summary line is counted from this group's stories via the same `briefingFacts`/`coverageMix`
 * derivations the page header uses — one vocabulary of facts, no new math.
 */
export function CategorySection({
  group,
  limit = 5,
  flip = false,
}: {
  group: TopicGroup;
  limit?: number;
  /** Mirror the layout (featured story on the right) — set on alternating modules for rhythm. */
  flip?: boolean;
}) {
  const { t, formatCompact } = useTranslation();
  const stories = group.stories.slice(0, limit);
  const [featured, ...supporting] = stories;
  if (!featured) return null;
  const facts = briefingFacts(stories);
  const mix = coverageMix(stories);
  const hasMix = mix.left + mix.center + mix.right > 0;

  const headingId = `category-${group.topic.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;

  return (
    <section aria-labelledby={headingId}>
      <SectionHeader
        id={headingId}
        title={group.topic}
        eyebrow={t("home.category.eyebrow")}
        href="/stories"
        actionLabel={t("home.viewAll")}
      />

      <div className="grid gap-5 lg:grid-cols-12">
        <div className={cn("lg:col-span-5", flip && "lg:order-last")}>
          <StoryFeatureCard story={featured} />
        </div>

        {supporting.length > 0 && (
          <ul className="divide-y lg:col-span-7">
            {supporting.map((story) => (
              <StoryListItem key={story.id} story={story} variant="compact" />
            ))}
          </ul>
        )}
      </div>

      {/* The topic's own coverage summary — the module ends on the product's question ("how is
          this topic being covered?"), not just a list of links. */}
      {hasMix && (
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border bg-card px-4 py-3">
          <div className="w-40 min-w-[8rem] flex-1 sm:flex-none" aria-hidden>
            <SpectrumBar distribution={mix} height={5} showLegend={false} />
          </div>
          <p className="text-xs text-muted-foreground">
            {t("home.category.mix", {
              events: formatCompact(facts.storyCount),
              publishers: formatCompact(facts.publisherCount),
            })}
          </p>
        </div>
      )}
    </section>
  );
}
