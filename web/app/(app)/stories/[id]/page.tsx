"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, EyeOff, Newspaper, Users } from "lucide-react";
import { useRecommendations, useSimilarStories, useStory } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { PageGrid } from "@/components/layout/page-grid";
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
import { StoryBreakdown } from "@/components/stories/breakdown/story-breakdown";
import { StoryTopics } from "@/components/stories/story-topics";
import { MAX_CARDS, SimilarStoriesPanel } from "@/components/stories/similar-stories-panel";
import { SimilarStories } from "@/components/stories/similar-stories";
import { RecommendationPanel } from "@/components/home/recommendation-panel";
import { LEAN_META } from "@ih/core/logic/metrics";
import { splitCoverage } from "@ih/core/logic/story-attached";
import { track, urlHost } from "@/lib/analytics";
import { useTranslation } from "@/lib/i18n";
import { useIsDesktop } from "@/lib/use-is-desktop";
import { formatDate } from "@ih/core/i18n/core";
import { activeLang } from "@/lib/active-lang";

const fmtDate = (iso?: string) =>
  iso ? formatDate(iso, activeLang(), { month: "short", day: "numeric", year: "numeric" }) : "";

/**
 * Story Details — the same editorial system as the home page, answering the reader's questions in
 * order: what happened (hero + the cluster's real summary as the standfirst), how it's covered
 * (filterable coverage list + the intelligence timeline), where coverage is thin (breakdown panel's
 * zero-side callouts), and what to read next (the engine's ranked similar stories).
 *
 * TWO COMPOSITIONS, ONE PAGE. The Similar Stories card in the companion rail is the DESKTOP story
 * view's answer to "what else covers this", and was specified for that view alone; below `lg` the
 * page is unchanged — "Picked for you" keeps the rail slot and the horizontal Similar Stories rail
 * still closes the page. `useIsDesktop` mounts exactly one of the two, the same way the home page
 * picks between its desktop and mobile compositions, so neither viewport pays for the other's
 * components or the other's queries.
 *
 * Queries: the story, its intelligence (inside StoryIntelligencePanel), and — for the Similar
 * Stories surfaces — ONE ranked query that returns at most MAX_CARDS stories. This page still does not
 * fetch the home page's 60-story list: the RUM investigation measured that at ~200 KB and ~a third
 * of this page's entire API transfer, all to pick a handful of cards, and on the entry paths that
 * matter most (a shared link, a notification tap) nothing has warmed the cache, so every such
 * visitor paid it. Scoring similarity where the catalog already lives is what lets the selection
 * see the whole catalog while the wire carries ten cards. That ONE query feeds whichever surface
 * the viewport mounts, so the card and the rail can never disagree about what is similar.
 */
export default function StoryDetailPage() {
  const { t, timeAgo } = useTranslation();
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const { data: story, isLoading, isError, error, refetch } = useStory(id);
  // Which composition this viewport gets. `null` until mounted — the server has no viewport — so
  // for one frame neither surface renders, which is what the page already shows while data loads.
  const desktop = useIsDesktop();
  // The Similar Stories rail, ranked by the engine over the WHOLE catalog.
  //
  // It replaced two queries composed on this page — a same-topic list plus the day's top six,
  // shown in that order — which is the defect the rail was reported for. Topic is a shelf, not a
  // subject, and "also busy today" is not a relationship at all: that is how a Venezuelan oil deal
  // came to sit beside a Supreme Court ruling about a ballroom, alike only in containing the word
  // "Trump". Re-ranking cannot repair a candidate pool chosen that way, and widening the pool here
  // would mean fetching the 60-story list this page deliberately does not fetch.
  const similar = useSimilarStories(id, MAX_CARDS);
  // Mobile only, and switched off elsewhere rather than skipped: the desktop rail shows Similar
  // Stories in this slot, and must not fetch a feed it will not render.
  const recommendations = useRecommendations(undefined, desktop === false);
  // A hero that errors mid-load hands the slot to the coverage masthead, exactly like absence —
  // but counted separately (story_hero_error), because the engine never downloads images and a
  // dead or hotlink-protected URL is only observable here. Reset if a refetch changes the URL.
  const [heroFailed, setHeroFailed] = React.useState(false);
  const heroSrc = story?.image;
  React.useEffect(() => setHeroFailed(false), [heroSrc]);

  // Rendered as given: already scored, cut and ranked. Anything below the engine's relative cut was
  // dropped there, so this can arrive SHORT or empty — which is the correct answer on a day with no
  // related coverage, rather than a gap to be filled with the day's top stories.
  //
  // The query's STATE goes with it, and must: `?? []` makes loading, failure and a genuine absence
  // indistinguishable downstream, and the rail has a different thing to say about each. Reporting
  // "nothing is similar" while the request is still in flight — or after it failed — would be the
  // component inventing a fact from a missing one.
  const related = similar.data?.stories ?? [];

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

  // MEMBER rows only for every fact this page derives itself (M4 containment, client half):
  // attached Tier B rows are coverage that never voted, and counting them into publisher stats,
  // the register split or framing would undo engine-side containment one .map() at a time. The
  // full list — members THEN the labeled addenda — belongs to CoverageList alone.
  const { panel: panelCoverage } = splitCoverage(story.coverage);
  const publisherCount = story.publisherCount ?? new Set(panelCoverage.map((c) => c.publisher)).size;
  const showHero = Boolean(story.image) && !heroFailed;

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
            {/* ONE breakdown card, three tabs (Bias · Factuality · Ownership) — this instance IS
                the mobile one: `lead` puts the whole rail directly under the hero when the grid
                collapses, so the phone gets the same three tabs in the flow that the desktop rail
                gets in its column. Bias and Ownership used to be two stacked cards asking two
                versions of the same question of the same outlets; tabs put all three answers in
                one place, and Factuality has somewhere to live. */}
            <StoryBreakdown story={story} />
            {/* No per-publisher tally here: the coverage list below names every publisher with its
                own headline and lean, and the breakdown above already carries the aggregate shape.
                A ranked repeat of the same names said nothing the page had not said twice. */}
            {/* What this story is ABOUT — the engine's ranked tags, each one a way into the other
                stories carrying it (story-topics.tsx). Placed above the reader's own feed because
                it belongs to the story: it answers "what is this" where the panel below answers
                "what else is for me". */}
            <StoryTopics story={story} />
            {/* DESKTOP: "Similar Stories" in the shell "Picked for you" established. It takes that
                card's place — on a story page the question "what else covers this" is the story's
                own, where a personalised feed is about the reader and has its own surface — and on
                this viewport it is the page's ONLY similar-stories surface, so the rail's
                three-state discipline (loading, failure, stated absence) lives here with the query
                state it needs.
                BELOW `lg`: the slot is unchanged — the reader's own feed, as before. */}
            {desktop ? (
              <SimilarStoriesPanel
                story={story}
                similar={related}
                isLoading={similar.isLoading}
                isError={similar.isError}
                onRetry={() => similar.refetch()}
              />
            ) : (
              recommendations.data && <RecommendationPanel recs={recommendations.data} />
            )}
          </>
        }
        lead={
          /* The hero is the grid's `lead` rather than the first of `children`, which is what lets
             the rail sit right under it on a phone instead of below the whole coverage list. The
             desktop grid is unchanged — see PageGrid. */
          <>
          {/* What happened — the hero, with the cluster's real summary as the standfirst.

              THE HEADLINE LEADS. With an image, the picture sits above it as on any front page.
              Without one, the COVERAGE MASTHEAD (coverage-plate.tsx) used to take the picture's
              slot — which put a 48px publisher count ABOVE the headline, so the first thing a
              reader met on an imageless story was a statistic about it rather than what it was.
              The plate now closes the block instead: kicker → headline → standfirst → dateline,
              then the coverage strip (its labelled L/C/R band and, on a gap story, the thin-side
              statement). Same counted facts, in reading order. The block's own spectrum bar and
              thin-side pill render only when the image is present, since the plate carries both.

              A hero URL that FAILS to load ends in the same masthead, tracked as
              story_hero_error so the failing hosts are measurable rather than guessed. */}
          <article className="overflow-hidden rounded-lg border bg-card shadow-soft">
            {showHero && (
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

              {/* Headline scale matches the home lead (hero-story.tsx): 34px bold, tight leading. */}
              <h1 className="text-balance text-[1.75rem] font-bold leading-[1.12] tracking-tight sm:text-[2.125rem]">
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

              {showHero && (
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
              )}
            </div>
            {!showHero && <CoveragePlate story={story} masthead />}
          </article>
          </>
        }
      >
          {/* Same event, side by side — the juxtaposition the filterable list below can never show.
              Renders nothing unless at least two rated sides actually wrote (lib/framing.ts). */}
          <FramingComparison coverage={panelCoverage} />

          {/* How is it covered — every article, filterable by the facets the data really has. */}
          <CoverageList coverage={story.coverage} />

          {/* What to read next, BELOW `lg` only — the engine's ranked same-event selection as a
              collapsible rail (similar-stories.tsx). It states an empty result instead of
              disappearing, so a story with genuinely nothing related reads as a decision rather
              than as a broken section. On desktop the rail card above IS this answer, and a second
              copy of it at the foot of the page would show the reader the same four stories
              twice. */}
          {desktop === false && (
            <SimilarStories
              stories={related}
              isLoading={similar.isLoading}
              isError={similar.isError}
              onRetry={() => similar.refetch()}
            />
          )}
      </PageGrid>
    </PageContainer>
  );
}
