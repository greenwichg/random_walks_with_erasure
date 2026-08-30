"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, EyeOff, Newspaper, Users } from "lucide-react";
import { useRecommendations, useStories, useStory } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { PageGrid } from "@/components/layout/page-grid";
import { SectionHeader } from "@/components/shared/section-header";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { ArticleImage } from "@/components/shared/article-image";
import { ShareButton } from "@/components/shared/share-button";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { CoveragePlate } from "@/components/stories/coverage-plate";
import { StoryIntelligencePanel } from "@/components/stories/story-intelligence-panel";
import { CoverageList } from "@/components/stories/coverage-list";
import { FramingComparison } from "@/components/stories/framing-comparison";
import { StoryCoveragePanel } from "@/components/stories/story-coverage-panel";
import { StoryListItem } from "@/components/home/story-list-item";
import { RecommendationPanel } from "@/components/home/recommendation-panel";
import { PublisherSpotlight } from "@/components/home/publisher-spotlight";
import { LEAN_META } from "@ih/core/logic/metrics";
import { track, urlHost } from "@/lib/analytics";
import { useTranslation } from "@/lib/i18n";
import { formatDate } from "@ih/core/i18n/core";
import { activeLang } from "@/lib/active-lang";

const fmtDate = (iso?: string) =>
  iso ? formatDate(iso, activeLang(), { month: "short", day: "numeric", year: "numeric" }) : "";

/**
 * Story Details — the same editorial system as the home page, answering the reader's questions in
 * order: what happened (hero + the cluster's real summary as the standfirst), how it's covered
 * (filterable coverage list + the intelligence timeline), where coverage is thin (breakdown panel's
 * zero-side callouts), and what to read next (related stories + the reader's own feed).
 *
 * Queries: the story, its intelligence (inside StoryIntelligencePanel), and — for the four-item
 * related module — two SMALL story queries instead of the home page's full 60-story list. The RUM
 * investigation measured that list at ~200 KB and ~a third of this page's entire API transfer, all
 * to pick 4 cards; on the entry paths that matter most (a shared link, a push-notification tap)
 * nothing has warmed the cache, so every such visitor paid it. The reader's recommendations reuse
 * their existing cached feed. No new endpoints — both queries are existing `/api/stories` filters.
 */
export default function StoryDetailPage() {
  const { t, timeAgo } = useTranslation();
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const { data: story, isLoading, isError, error, refetch } = useStory(id);
  // Related coverage, same editorial rule as before — same topic first, then the day's top events,
  // never this story itself — served by two bounded queries whose limits are worst-case sized: the
  // topic query needs 4 after excluding self (5 covers it), and the top-6 fill still yields 4 when
  // every one of its members is either self or a topic duplicate. One deliberate edge improved: a
  // same-topic story ranked below the old top-60 window is now eligible — deeper topic coverage,
  // same intent. The topic query waits for the story (its input); the fill query fires at once.
  const topStories = useStories({ sort: "top", limit: 6 });
  const topicStories = useStories(
    { topic: story?.topic, sort: "top", limit: 5 },
    { enabled: !!story?.topic },
  );
  const recommendations = useRecommendations();
  // A hero that errors mid-load hands the slot to the coverage masthead, exactly like absence —
  // but counted separately (story_hero_error), because the engine never downloads images and a
  // dead or hotlink-protected URL is only observable here. Reset if a refetch changes the URL.
  const [heroFailed, setHeroFailed] = React.useState(false);
  const heroSrc = story?.image;
  React.useEffect(() => setHeroFailed(false), [heroSrc]);

  // Wait for the topic query to settle before composing, or the top-6 fill — which usually lands
  // first now — would paint an unprioritised list and visibly reshuffle when the topic results
  // arrive. The old code never showed that (its one big query carried both halves at once), and a
  // below-fold module appearing ~100 ms later is better than one that rearranges itself.
  const topicSettled = !story?.topic || topicStories.isSuccess || topicStories.isError;
  const related = React.useMemo(() => {
    if (!topicSettled) return [];
    const seen = new Set([id]);
    const merged = [];
    for (const s of [...(topicStories.data?.stories ?? []), ...(topStories.data?.stories ?? [])]) {
      if (!seen.has(s.id)) {
        seen.add(s.id);
        merged.push(s);
      }
    }
    return merged.slice(0, 4);
  }, [topicSettled, topicStories.data, topStories.data, id]);

  const back = (
    <Link
      href="/stories"
      className="inline-flex items-center gap-1.5 rounded text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      <ArrowLeft className="h-4 w-4" aria-hidden /> {t("stories.back")}
    </Link>
  );

  if (isLoading) {
    return (
      <PageContainer>
        <div className="mb-5">{back}</div>
        <div aria-hidden>
          <PageGrid
            rail={
              <>
                <Skeleton className="h-48 w-full rounded-lg" />
                <Skeleton className="h-72 w-full rounded-lg" />
              </>
            }
          >
            <Skeleton className="aspect-[21/9] w-full rounded-lg" />
            <Skeleton className="h-40 w-full rounded-lg" />
            <div className="space-y-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-20 rounded-md" />
              ))}
            </div>
          </PageGrid>
        </div>
      </PageContainer>
    );
  }
  if (isError) {
    // A 404 is not "something went wrong" — the event dissolved when the catalog window moved
    // past it, which is exactly where a days-old breaking-news notification's deep link lands.
    // Retrying can never load it, so that path gets the page's own "story not found" state
    // (the same one the null branch below renders) instead of a retry button to nowhere.
    if ((error as { status?: number } | null)?.status === 404) {
      return (
        <PageContainer>
          <div className="mb-5">{back}</div>
          <EmptyState
            icon={Newspaper}
            title={t("stories.notFound.title")}
            description={t("stories.notFound.body")}
          />
        </PageContainer>
      );
    }
    return (
      <PageContainer>
        <div className="mb-5">{back}</div>
        <ErrorState onRetry={() => refetch()} />
      </PageContainer>
    );
  }
  if (!story) {
    return (
      <PageContainer>
        <div className="mb-5">{back}</div>
        <EmptyState
          icon={Newspaper}
          title={t("stories.notFound.title")}
          description={t("stories.notFound.body")}
        />
      </PageContainer>
    );
  }

  const publisherCount = story.publisherCount ?? new Set(story.coverage.map((c) => c.publisher)).size;
  const publisherCounts = (() => {
    const counts = new Map<string, number>();
    for (const row of story.coverage) counts.set(row.publisher, (counts.get(row.publisher) ?? 0) + 1);
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, 8)
      .map(([publisher, articles]) => ({ publisher, stories: articles }));
  })();

  return (
    <PageContainer>
      {/* Breadcrumb row: the way back on the left, actions on the right. */}
      <div className="mb-5 flex items-center justify-between gap-3">
        {back}
        <ShareButton title={story.title} />
      </div>

      <PageGrid
        rail={
          /* Companion rail: how is this story MOVING, how balanced is it, who's on it, what next
             for YOU. Story Intelligence leads because "is this still developing?" is the question a
             reader asks before "is the coverage balanced?" — and because it was previously below a
             40-row article list, at a scroll depth almost nobody reached. */
          <>
            <StoryIntelligencePanel storyId={story.id} />
            <StoryCoveragePanel distribution={story.distribution} coverage={story.coverage} />
            <PublisherSpotlight
              publishers={publisherCounts}
              titleKey="story.publishersTitle"
              countKey="stories.articlesCount"
            />
            {recommendations.data && <RecommendationPanel recs={recommendations.data} />}
          </>
        }
      >
          {/* What happened — the hero, with the cluster's real summary as the standfirst. An
              imageless story opens with the COVERAGE MASTHEAD instead (coverage-plate.tsx):
              before it, the hero simply self-hid and the page started abruptly at the topic
              label — the one no-image surface that had no designed state at all. A hero URL
              that FAILS to load ends in the same masthead, tracked as story_hero_error so the
              failing hosts are measurable rather than guessed. */}
          <article className="overflow-hidden rounded-lg border bg-card shadow-soft">
            {story.image && !heroFailed ? (
              <ArticleImage
                src={story.image}
                alt={story.title}
                priority
                aspect="aspect-[21/9]"
                className="w-full rounded-none border-0"
                onHidden={() => {
                  setHeroFailed(true);
                  track("story_hero_error", { host: urlHost(story.image), surface: "detail" });
                }}
              />
            ) : (
              <CoveragePlate story={story} masthead />
            )}
            <div className="p-5">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                {story.topic && (
                  <span className="text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground">
                    {story.topic}
                  </span>
                )}
                {story.freshness && (
                  <FreshnessBadge band={story.freshness.band} score={story.freshness.score} />
                )}
              </div>

              <h1 className="text-balance text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">
                {story.title}
              </h1>

              {story.summary && (
                <p className="mt-2.5 max-w-2xl text-sm leading-relaxed text-muted-foreground sm:text-base">
                  {story.summary}
                </p>
              )}

              <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1">
                  <Users className="h-3.5 w-3.5" aria-hidden />
                  {t("stories.publishers", { n: publisherCount })}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Newspaper className="h-3.5 w-3.5" aria-hidden />
                  {t("stories.articlesCount", { n: story.totalCoverage })}
                </span>
                {story.earliest && <span>{t("stories.firstReport", { date: fmtDate(story.earliest) })}</span>}
                {story.latest && story.latest !== story.earliest && (
                  <span>{t("stories.latestReport", { date: fmtDate(story.latest) })}</span>
                )}
                {story.updatedAt && <span>{timeAgo(story.updatedAt)}</span>}
              </div>

              <div className="mt-4 max-w-md">
                <SpectrumBar distribution={story.distribution} height={10} />
                {story.blindspotSide && (
                  <p
                    className="mt-2 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[0.68rem] font-medium"
                    style={{
                      color: LEAN_META[story.blindspotSide].color,
                      borderColor: LEAN_META[story.blindspotSide].color,
                    }}
                  >
                    <EyeOff className="h-3 w-3" aria-hidden />
                    {t("stories.thinCoverage", { side: t(`filter.${story.blindspotSide}`).toLowerCase() })}
                  </p>
                )}
              </div>
            </div>
          </article>

          {/* Same event, side by side — the juxtaposition the filterable list below can never show.
              Renders nothing unless at least two rated sides actually wrote (lib/framing.ts). */}
          <FramingComparison coverage={story.coverage} />

          {/* How is it covered — every article, filterable by the facets the data really has. */}
          <CoverageList coverage={story.coverage} />

          {/* What to read next — same-topic first, from the same cached top-stories page. */}
          {related.length > 0 && (
            <section aria-labelledby="related-heading">
              <SectionHeader
                id="related-heading"
                title={t("story.related")}
                href="/stories"
                actionLabel={t("home.viewAll")}
              />
              <ul className="divide-y">
                {related.map((s) => (
                  <StoryListItem key={s.id} story={s} variant="compact" showImage />
                ))}
              </ul>
            </section>
          )}
      </PageGrid>
    </PageContainer>
  );
}
