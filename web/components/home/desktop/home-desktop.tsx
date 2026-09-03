"use client";

import * as React from "react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { Check, ChevronLeft, ChevronRight, EyeOff, Newspaper, Plus } from "lucide-react";
import type { Article, DashboardSummary, Story } from "@ih/core/domain/types";
import type { TopicGroup } from "@ih/core/logic/home";
import { countryName } from "@ih/core/logic/countries";
import { useReport, useSearch, useSettings } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { ArticleImage } from "@/components/shared/article-image";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { useReadArticleAction } from "@/components/shared/read-article-button";
import { Button } from "@/components/ui/button";
import { CoveragePlate } from "@/components/stories/coverage-plate";
import { HomeSkeleton } from "@/components/home/home-skeleton";
import { BiasStrip } from "@/components/home/desktop/bias-strip";
import { StoryRow } from "@/components/home/desktop/story-row";
import type { HomeModel } from "@/components/home/home-model";
import { useTranslation } from "@/lib/i18n";
import { activeLang } from "@/lib/active-lang";
import { cn } from "@/lib/utils";

/*
 * The DESKTOP FRONT PAGE (lg+), composed to the reference layout and fed by Hidden View's own
 * data (docs/DESKTOP_EDITORIAL_AUDIT.md, part 3):
 *
 *   topic strip                       — the day's real catalog topics, filtering the page in place
 *   ┌ Briefing + News stories │ Lead + stories with thumbnails │ Blind spots + My news bias ┐
 *   ├ Stories with thumbnails                                  │ Daily local news          ┤
 *   ├ {Topic} news: latest big card + {topic} blind spots                                  ┤
 *   ├ Latest stories                                           │ Similar news topics       ┤
 *   ├ {Topic} news (second topic)                                                          ┤
 *   └ Latest news stories + More stories                                                   ┘
 *
 * Every number is counted from the one `/api/stories` page (home-model.ts); the reader modules
 * read the dashboard, the report and settings — queries the shell already holds. Nothing here is
 * fetched for the layout's sake. Below lg the page is home-mobile.tsx, untouched.
 */

const SECTION_TITLE = "text-[19px] font-semibold leading-tight tracking-tight";
const LABEL = "text-[13px] font-medium text-muted-foreground";
const OUTLINE_BTN = "h-8 rounded-md px-3 text-[13px] font-medium";

/**
 * Hands out stories in page order. Every module prefers events no module above has shown; the
 * lower runs (`reuse`) top themselves up from their own candidate order once the page's fresh
 * events run out, so a small day still composes as a full front page — the reference layout
 * repeats an event across its sections too, at different positions — while a large day never
 * repeats one. A list never contains the same event twice.
 */
function allocator() {
  const used = new Set<string>();
  const take = (candidates: Story[], n: number, reuse = false) => {
    const out: Story[] = [];
    const seen = new Set<string>();
    for (const s of candidates) {
      if (out.length >= n) break;
      if (seen.has(s.id) || used.has(s.id)) continue;
      seen.add(s.id);
      used.add(s.id);
      out.push(s);
    }
    if (reuse) {
      for (const s of candidates) {
        if (out.length >= n) break;
        if (seen.has(s.id)) continue;
        seen.add(s.id);
        out.push(s);
      }
    }
    return out;
  };
  return { take, mark: (s: Story) => used.add(s.id) };
}

export function HomeDesktop({
  model,
  dashboard,
  loading,
  error,
  onRetry,
}: {
  model: HomeModel;
  dashboard: DashboardSummary | undefined;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const { rail, topic, setTopic, visible, facts, hero, topStories, blindspots, categories, latest } = model;

  // Page order: lead → blind-spot cards → side column → centre column → second band → topic
  // sections → closing lists. The blind-spot cards are allocated before the lists so the rail's
  // own signal is never pre-empted by a row that happened to be listed first.
  const plan = React.useMemo(() => {
    const a = allocator();
    if (hero) a.mark(hero);
    const spots = a.take(blindspots, 2);
    const side = a.take(topStories, 5);
    const centre = a.take([...topStories, ...latest], 6);
    const band = a.take([...latest, ...visible], 4, true);
    const sections: { group: TopicGroup; lead: Story; gaps: Story[] }[] = [];
    for (const group of categories.slice(0, 2)) {
      const lead = a.take(group.stories, 1)[0] ?? group.stories[0];
      if (!lead) continue;
      const rest = group.stories.filter((s) => s.id !== lead.id);
      const gaps = a.take([...rest.filter((s) => s.blindspotSide), ...rest], 2, true);
      sections.push({ group, lead, gaps });
    }
    const latestList = a.take([...latest, ...visible], 6, true);
    const closing = a.take(visible, 5, true);
    return { side, centre, band, spots, sections, latestList, closing };
  }, [visible, hero, topStories, latest, blindspots, categories]);

  return (
    <>
      <TopicStrip topics={rail} active={topic} onSelect={setTopic} />
      <PageContainer className="pt-5 lg:pt-5">
        {loading && <HomeSkeleton />}
        {error && <ErrorState onRetry={onRetry} />}
        {!loading && !error && visible.length === 0 && (
          <EmptyState icon={Newspaper} title={t("home.empty.title")} description={t("home.empty.body")} />
        )}

        {visible.length > 0 && (
          <>
            {/* Row 1 — three columns */}
            <div className="grid grid-cols-12 gap-6">
              <div className="col-span-3 min-w-0">
                <Briefing facts={facts} />
                {plan.side.length > 0 && (
                  <section aria-labelledby="news-stories-heading" className="mt-6">
                    <h2 id="news-stories-heading" className={cn(SECTION_TITLE, "mb-1")}>
                      {t("home.newsStories")}
                    </h2>
                    <ul>
                      {plan.side.map((s) => (
                        <StoryRow key={s.id} story={s} size="sm" />
                      ))}
                    </ul>
                  </section>
                )}
              </div>

              <div className="col-span-6 min-w-0">
                {hero && <LeadStory story={hero} />}
                {plan.centre.length > 0 && (
                  <ul className="mt-4 border-t">
                    {plan.centre.map((s) => (
                      <StoryRow key={s.id} story={s} size="md" thumb />
                    ))}
                  </ul>
                )}
              </div>

              <div className="col-span-3 min-w-0">
                <BlindspotRail stories={plan.spots} />
                <MyNewsBias dashboard={dashboard} />
              </div>
            </div>

            <hr className="my-8" />

            {/* Row 2 — stories with thumbnails beside the local module */}
            <div className="grid grid-cols-12 gap-6">
              <div className="col-span-9 min-w-0">
                <ul className="-mt-3">
                  {plan.band.map((s) => (
                    <StoryRow key={s.id} story={s} size="md" thumb />
                  ))}
                </ul>
              </div>
              <div className="col-span-3 min-w-0 border-l pl-6">
                <LocalNews />
              </div>
            </div>

            {plan.sections[0] && <TopicSection {...plan.sections[0]} />}

            {/* Latest stories beside the topic index */}
            <hr className="my-8" />
            <div className="grid grid-cols-12 gap-6">
              <div className="col-span-9 min-w-0">
                <h2 className={cn(SECTION_TITLE, "mb-1")}>{t("home.latest.title")}</h2>
                <ul>
                  {plan.latestList.map((s) => (
                    <StoryRow key={s.id} story={s} size="sm" />
                  ))}
                </ul>
              </div>
              <div className="col-span-3 min-w-0 border-l pl-6">
                <SimilarTopics topics={rail} active={topic} onSelect={setTopic} />
              </div>
            </div>

            {plan.sections[1] && <TopicSection {...plan.sections[1]} />}

            {/* Closing run */}
            <hr className="my-8" />
            <section aria-labelledby="latest-news-heading">
              <h2 id="latest-news-heading" className={cn(SECTION_TITLE, "mb-1")}>
                {t("home.latestNewsStories")}
              </h2>
              <ul className="max-w-3xl">
                {plan.closing.map((s) => (
                  <StoryRow key={s.id} story={s} size="sm" />
                ))}
              </ul>
              <Button asChild variant="outline" size="sm" className={cn(OUTLINE_BTN, "mt-5")}>
                <Link href="/stories?sort=latest">{t("home.moreStories")}</Link>
              </Button>
            </section>
          </>
        )}
      </PageContainer>
    </>
  );
}

/* ---------------------------------------------------------------------------------------------- */

/** The reference's topic chips under the bar: the day's real topics, scrolling, with arrows. */
function TopicStrip({
  topics,
  active,
  onSelect,
}: {
  topics: { topic: string; count: number }[];
  active: string | null;
  onSelect: (topic: string | null) => void;
}) {
  const { t } = useTranslation();
  const ref = React.useRef<HTMLDivElement>(null);
  const scroll = (dir: 1 | -1) => ref.current?.scrollBy({ left: dir * 320, behavior: "smooth" });
  if (topics.length === 0) return null;

  const chip = (label: string, on: boolean, onClick: () => void, key: string) => (
    <button
      key={key}
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(
        "shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-[12px] font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        on ? "border-foreground bg-foreground text-background" : "border-border bg-card text-foreground/80 hover:bg-accent",
      )}
    >
      {label}
    </button>
  );

  return (
    <div className="border-b">
      <div className="mx-auto flex h-11 w-full max-w-6xl items-center gap-2 px-8">
        <button type="button" aria-hidden tabIndex={-1} onClick={() => scroll(-1)} className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground">
          <ChevronLeft className="h-4 w-4" />
        </button>
        <div
          ref={ref}
          role="toolbar"
          aria-label={t("home.trending.title")}
          className="flex min-w-0 flex-1 gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {chip(t("home.trending.all"), active === null, () => onSelect(null), "__all")}
          {topics.map((entry) =>
            chip(entry.topic, active === entry.topic, () => onSelect(active === entry.topic ? null : entry.topic), entry.topic),
          )}
        </div>
        <button type="button" aria-hidden tabIndex={-1} onClick={() => scroll(1)} className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground">
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

/** "Briefing" — the product's own counted opening statement, in the reference's left-column slot. */
function Briefing({ facts }: { facts: HomeModel["facts"] }) {
  const { t, timeAgo, formatCompact } = useTranslation();
  const flagged = facts.blindspotCount > 0;
  return (
    <section aria-labelledby="briefing-heading" className="border-b pb-4">
      <h2 id="briefing-heading" className={cn(SECTION_TITLE, "mb-2")}>
        {t("home.briefing.title")}
      </h2>
      <p className="text-[15px] font-semibold leading-snug tracking-tight">
        {flagged
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
        <Link href="/analyze" className="font-medium text-foreground/80 hover:text-foreground">
          {t("home.briefing.analyze")}
        </Link>
      </div>
    </section>
  );
}

/** The lead: picture (or the coverage plate), the headline at display scale, the labelled strip. */
function LeadStory({ story }: { story: Story }) {
  const { t, formatCompact } = useTranslation();
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [story.image]);
  const showImage = Boolean(story.image) && !failed;
  const publisherCount = story.publisherCount ?? story.publishers?.length ?? null;

  return (
    <article className="group">
      <Link href={`/stories/${story.id}`} className="block rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
        {showImage ? (
          <ArticleImage
            src={story.image}
            alt={story.title}
            priority
            aspect="aspect-[16/9]"
            className="rounded-md"
            onHidden={() => setFailed(true)}
          />
        ) : (
          <CoveragePlate story={story} className="mb-0" />
        )}
        {/* The plate already carries the labelled band; only a picture needs the strip under it. */}
        {showImage && (
          <div className="mt-3">
            <BiasStrip distribution={story.distribution} labels />
          </div>
        )}
        <h2 className="mt-3 text-balance text-[26px] font-bold leading-[1.15] tracking-tight transition-colors group-hover:text-primary">
          {story.title}
        </h2>
        <p className="mt-2 text-[12px] text-muted-foreground">
          {[story.topic, t("storyCard.sources", { n: formatCompact(story.totalCoverage) }), publisherCount != null ? t("stories.publishers", { n: formatCompact(publisherCount) }) : ""]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </Link>
    </article>
  );
}

/** A small picture card — the blind-spot and topic-gap cards of the reference. */
function SpotCard({ story, showTopic = true }: { story: Story; showTopic?: boolean }) {
  const { timeAgo } = useTranslation();
  const kicker = [showTopic ? story.topic : "", story.updatedAt ? timeAgo(story.updatedAt) : ""].filter(Boolean).join(" · ");
  return (
    <li className="group">
      <Link href={`/stories/${story.id}`} className="block rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
        {story.image ? (
          <>
            <ArticleImage src={story.image} alt="" aspect="aspect-[16/9]" className="rounded-md" />
            <div className="mt-2">
              <BiasStrip distribution={story.distribution} labels />
            </div>
          </>
        ) : (
          <CoveragePlate story={story} className="mb-0 p-3" />
        )}
        {kicker && <p className="mt-1.5 text-[11px] text-muted-foreground">{kicker}</p>}
        <h3 className="mt-1 text-[13px] font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
          {story.title}
        </h3>
      </Link>
    </li>
  );
}

/** The right column's blind-spot module: mark, definition, two cards, the feed button. */
function BlindspotRail({ stories }: { stories: Story[] }) {
  const { t } = useTranslation();
  return (
    <section aria-labelledby="blindspot-rail-heading" className="border-b pb-5">
      <h2 id="blindspot-rail-heading" className="inline-flex items-center gap-2 font-sans text-[15px] font-bold uppercase tracking-wide">
        <EyeOff className="h-5 w-5" aria-hidden />
        {t("home.blindspots.title")}
      </h2>
      <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">{t("home.blindspots.description")}</p>
      {stories.length > 0 && (
        <ul className="mt-4 space-y-5">
          {stories.map((s) => (
            <SpotCard key={s.id} story={s} />
          ))}
        </ul>
      )}
      <Button asChild variant="outline" size="sm" className={cn(OUTLINE_BTN, "mt-5 w-full")}>
        <Link href="/stories?blindspot=any">{t("home.blindspots.viewFeed")}</Link>
      </Button>
    </section>
  );
}

/** "My news bias" — the reader's own left/centre/right split from their Health Report. */
function MyNewsBias({ dashboard }: { dashboard: DashboardSummary | undefined }) {
  const { t, formatCompact } = useTranslation();
  const { data: session } = useSession();
  const report = useReport();
  const measured = report.data && !report.data.sample ? report.data.viewpoint : null;
  const reads = dashboard?.coverage?.reads ?? report.data?.coverage?.reads ?? 0;
  const days = dashboard?.streakDays ?? 0;

  return (
    <section aria-labelledby="my-bias-heading" className="pt-5">
      <h2 id="my-bias-heading" className={SECTION_TITLE}>
        {t("home.myBias.title")}
      </h2>
      <p className="mt-2 text-[14px] font-semibold">{session?.user?.name ?? t("home.menu.myAccount")}</p>
      <p className="text-[12px] text-muted-foreground">
        {t("home.myBias.stats", { articles: formatCompact(reads), days: formatCompact(days) })}
      </p>
      <div className="mt-3">
        {measured ? (
          <BiasStrip distribution={measured} labels />
        ) : (
          <p className="text-[12px] text-muted-foreground">{t("home.myBias.empty")}</p>
        )}
      </div>
      <Button asChild variant="outline" size="sm" className={cn(OUTLINE_BTN, "mt-4 w-full")}>
        <Link href="/history">{t("home.myBias.cta")}</Link>
      </Button>
    </section>
  );
}

/** A located headline that records the read through the shared pipeline, then opens the article. */
function LocalHeadline({ article }: { article: Article }) {
  const { open, actionable } = useReadArticleAction(article, "home");
  return (
    <li className="border-b py-3 last:border-b-0">
      <button
        type="button"
        onClick={open}
        disabled={!actionable}
        className="w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="block text-[11px] text-muted-foreground">{article.publisher}</span>
        <span className="mt-0.5 block font-display text-[14px] font-semibold leading-snug tracking-tight hover:text-primary">
          {article.headline}
        </span>
      </button>
    </li>
  );
}

/** "Daily local news" — the reader's edition, from settings; a setup pointer until they pick one. */
function LocalNews() {
  const { t } = useTranslation();
  const settings = useSettings();
  const place =
    settings.data?.edition ?? settings.data?.locations?.find((l) => l.level === "country")?.placeId ?? null;
  const articles = useSearch({ country: place ?? undefined, sort: "newest", limit: 3 }, place != null);
  const results = articles.data?.results ?? [];

  return (
    <section aria-labelledby="local-news-heading">
      <h2 id="local-news-heading" className={SECTION_TITLE}>
        {place ? t("home.pulse.title", { place: countryName(place, activeLang()) }) : t("home.local.title")}
      </h2>
      {place == null ? (
        <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
          {t("home.pulse.setupBody")}{" "}
          <Link href="/settings" className="font-medium text-foreground hover:underline">
            {t("nav.settings")}
          </Link>
        </p>
      ) : results.length > 0 ? (
        <ul className="mt-1">
          {results.map((a) => (
            <LocalHeadline key={a.id} article={a} />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-[12px] text-muted-foreground">{t("local.noArticles.body")}</p>
      )}
      <Button asChild variant="outline" size="sm" className={cn(OUTLINE_BTN, "mt-4 w-full")}>
        <Link href={place ? `/stories?country=${encodeURIComponent(place)}` : "/settings"}>{t("common.readMore")}</Link>
      </Button>
    </section>
  );
}

/** A topic section: "{Topic} news" — the latest event as a big card, its coverage gaps beside it. */
function TopicSection({ group, lead, gaps }: { group: TopicGroup; lead: Story; gaps: Story[] }) {
  const { t } = useTranslation();
  const href = `/stories?topic=${encodeURIComponent(group.topic)}`;
  const headingId = `topic-${group.topic.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section aria-labelledby={headingId} className="mt-8 border-t pt-6">
      <div className="mb-4 flex items-center justify-between gap-4">
        <h2 id={headingId} className="text-[22px] font-bold leading-tight tracking-tight">
          {t("home.topic.section", { topic: group.topic })}
        </h2>
        <Button asChild variant="outline" size="sm" className={OUTLINE_BTN}>
          <Link href={href}>{t("common.readMore")}</Link>
        </Button>
      </div>
      <div className="grid grid-cols-12 gap-6">
        <div className="col-span-7 min-w-0">
          <p className={cn(LABEL, "mb-3")}>{t("home.topic.latest", { topic: group.topic })}</p>
          <article className="group">
            <Link href={`/stories/${lead.id}`} className="block rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
              {lead.image ? (
                <>
                  <ArticleImage src={lead.image} alt={lead.title} aspect="aspect-[16/9]" className="rounded-md" />
                  <div className="mt-3">
                    <BiasStrip distribution={lead.distribution} labels />
                  </div>
                </>
              ) : (
                <CoveragePlate story={lead} className="mb-0" />
              )}
              <h3 className="mt-3 text-balance text-[26px] font-bold leading-[1.15] tracking-tight transition-colors group-hover:text-primary">
                {lead.title}
              </h3>
            </Link>
          </article>
        </div>
        <div className="col-span-5 min-w-0 border-l pl-6">
          <p className={cn(LABEL, "mb-3")}>{t("home.topic.blindspots", { topic: group.topic })}</p>
          <ul className="grid grid-cols-2 gap-4">
            {gaps.map((s) => (
              <SpotCard key={s.id} story={s} showTopic={false} />
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

/** "Similar news topics" — the topic index with the reference's plus/check marks; the same in-page
 *  filter the strip drives, so the two can never disagree. */
function SimilarTopics({
  topics,
  active,
  onSelect,
}: {
  topics: { topic: string; count: number }[];
  active: string | null;
  onSelect: (topic: string | null) => void;
}) {
  const { t, formatCompact } = useTranslation();
  if (topics.length === 0) return null;
  return (
    <section aria-labelledby="similar-topics-heading">
      <h2 id="similar-topics-heading" className={SECTION_TITLE}>
        {t("home.similarTopics")}
      </h2>
      <ul className="mt-2">
        {topics.map((entry) => {
          const on = active === entry.topic;
          return (
            <li key={entry.topic} className="border-b last:border-b-0">
              <button
                type="button"
                aria-pressed={on}
                onClick={() => onSelect(on ? null : entry.topic)}
                className="flex w-full items-center justify-between gap-3 py-2.5 text-left text-[14px] transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="min-w-0 truncate font-medium">{entry.topic}</span>
                <span className="inline-flex shrink-0 items-center gap-2 text-[11px] tabular-nums text-muted-foreground">
                  {formatCompact(entry.count)}
                  {on ? <Check className="h-4 w-4 text-foreground" aria-hidden /> : <Plus className="h-4 w-4" aria-hidden />}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
