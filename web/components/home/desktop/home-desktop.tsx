"use client";

import * as React from "react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { Check, ChevronLeft, ChevronRight, EyeOff, Newspaper, Plus } from "lucide-react";
import type { Article, DashboardSummary, Story } from "@ih/core/domain/types";
import type { PublisherCount, TopicGroup } from "@ih/core/logic/home";
import { countryName } from "@ih/core/logic/countries";
import { useReport, useSearch, useSettings } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { CardImage } from "@/components/shared/card-image";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { useReadArticleAction } from "@/components/shared/read-article-button";
import { Button } from "@/components/ui/button";
import { HomeSkeleton } from "@/components/home/home-skeleton";
import { BiasStrip } from "@/components/shared/bias-strip";
import { StoryRow } from "@/components/shared/story-row";
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
 *
 * That shape is the FULL day. It is also what the topic strip filters down to two events, or one,
 * and the page has to hold together at every size in between. Three rules do that, each with its
 * own note below: a row's columns size to their own content (`ROW`), row 1's three columns share a
 * thin day in proportion rather than in code order (`plan`), and a run with nothing new to add does
 * not render at all — the two reader modules pairing off into one row of halves when that leaves
 * them without a list to sit beside (`railsPaired`). A day big enough to fill row 1 and the runs
 * below reaches none of them and composes exactly as it always has.
 */

const SECTION_TITLE = "text-[19px] font-semibold leading-tight tracking-tight";
const LABEL = "text-[13px] font-medium text-muted-foreground";
const OUTLINE_BTN = "h-8 rounded-md px-3 text-[13px] font-medium";
/** Every module is a tile on the page surface (globals.css, desktop surfaces): card, hairline, 16px. */
const TILE = "rounded-md border bg-card p-4";
/**
 * A page row's columns each size to their OWN content.
 *
 * Grid's default is `stretch`, which sets every item in a row to the tallest one. Where the two
 * items are bordered tiles that is not balance, it is a void: the shorter module keeps its content
 * at the top and the card's border runs on past it. Measured on the demo catalog at 1440px, one
 * card at a time — 680px of empty card under the lead on the unfiltered page, 370px under "Similar
 * news topics", 270px under "Daily local news", 430px under the lead on Arts. A sparse category is
 * where it shows worst, because that is where the two sides of a row differ most.
 *
 * Ragged column bottoms are what a front page is supposed to do; empty bordered rectangles are not.
 * The one row that keeps stretching is the topic section's, whose `border-l` is a real divider and
 * has to span both halves — see `TopicSection`.
 */
const ROW = "grid grid-cols-12 items-start gap-6";

/* Row 1's ceilings, in the order the columns are served. A full day fills all three; see `plan`. */
/** Picture cards in the blind-spot rail. */
const SPOT_CARDS = 3;
/** Thumbed rows under the lead, in the centre column. */
const CENTRE_ROWS = 6;
/** Headline rows in the left column's "News stories". */
const SIDE_ROWS = 7;
/**
 * Publishers listed when a thin day leaves the left column short (`TodaysPublishers`).
 *
 * Three, because the column is filling a gap and must not become the gap. The model offers six;
 * all six measure ~330px and made the left column the TALLEST of row 1 on three of four demo
 * categories — 133px past the lead, which inverts the hierarchy the featured column exists to
 * hold. Three measures ~198px and lands inside the gap on every one of them: the shortfall under
 * News stories goes from 286px to 88px on a one-event category and from 213px to ~15px on the
 * full page, without ever out-topping the lead.
 */
const PUBLISHER_ROWS = 3;

/**
 * Hands out stories in page order: every module gets events no module above it has shown, and a
 * list never contains the same event twice.
 *
 * `n` is a CEILING, never a quota. A module that cannot be filled renders short, and a module that
 * cannot be filled at all does not render — because the runs down the page exist to carry the
 * reader PAST what they have already seen, and a run with nothing new is the same headlines under
 * a new title. On a one-story category that came out as the story four times over: once as the
 * lead, then again under the thumbed band, "Latest stories" and "Latest news stories". Longer, not
 * fuller.
 *
 * `reuse` is the one exception, and it is left to the topic sections' gap cards. A topic module is
 * a different lens on one topic rather than the next screenful of the page, so "{Topic} blind
 * spots" showing an event the general list also carries is the module working, not a repeat — the
 * reference does the same. Everywhere else the page would rather be short than say it twice.
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
  const { rail, topic, setTopic, visible, facts, publishers, hero, topStories, blindspots, categories, latest } = model;

  // Page order: lead → blind-spot cards → centre column → side column → second band → topic
  // sections → closing lists.
  const plan = React.useMemo(() => {
    const a = allocator();
    if (hero) a.mark(hero);
    // Row 1's three columns share ONE day's stories, and a thin category cannot fill all three.
    // Who goes short used to be decided by code order: the two lists were served first and took
    // everything they could hold, so a category with a handful of events left the CENTRE — the
    // featured column, the one the page is built around — holding nothing but its lead. Measured
    // on the demo catalog at 1440px: seven headline rows stacked in the left rail, three picture
    // cards in the right, and 680px of empty card under the lead between them.
    //
    // So the two SHAPED modules are budgeted against how much of a full row the day can actually
    // supply, and the plain headline list absorbs whatever they leave. `share` is 1 on a full day,
    // which reproduces the previous 3/6/7 exactly — this changes thin categories only. The centre
    // keeps a floor of one row because a lead with a row under it still reads as a column, while a
    // lead alone reads as a card someone forgot to finish.
    //
    // Order is editorial, not arithmetic: blind spots first (they draw from an already-filtered
    // pool and the rail's own signal must never be pre-empted), then the featured column, then
    // the list. These stay ceilings — `take` returns what exists, never padding to reach one.
    const share = Math.min(1, Math.max(0, visible.length - 1) / (SPOT_CARDS + CENTRE_ROWS + SIDE_ROWS));
    const spots = a.take(blindspots, Math.round(SPOT_CARDS * share));
    const centre = a.take([...topStories, ...latest], Math.max(1, Math.round(CENTRE_ROWS * share)));
    // Same candidates as the centre, not `topStories` alone. `topStories` is eight events wide, so
    // once the centre is served first it can leave fewer than eight behind — measured on a
    // 18-event day, the left column came up empty and "News stories" stopped rendering. Reaching
    // into the recency run behind it is what the module already shows further down the page, and
    // it means the column is short only when the DAY is short, not when the row above it was.
    const side = a.take([...topStories, ...latest], SIDE_ROWS);
    const band = a.take([...latest, ...visible], 4);
    const sections: { group: TopicGroup; lead: Story; gaps: Story[] }[] = [];
    for (const group of categories.slice(0, 2)) {
      const lead = a.take(group.stories, 1)[0] ?? group.stories[0];
      if (!lead) continue;
      const rest = group.stories.filter((s) => s.id !== lead.id);
      const gaps = a.take([...rest.filter((s) => s.blindspotSide), ...rest], 2, true);
      sections.push({ group, lead, gaps });
    }
    const latestList = a.take([...latest, ...visible], 6);
    const closing = a.take(visible, 5);
    return { side, centre, band, spots, sections, latestList, closing };
  }, [visible, hero, topStories, latest, blindspots, categories]);

  // Each run below row 1 renders only if the day still had events for it. Losing one would strand
  // the reader module beside it, so when either row loses its list the two modules pair off into
  // one row of halves instead — the page keeps them both, at a width they read better at than the
  // 3-column rail. A day with more events than row 1 can hold never reaches this: the unfiltered
  // page places 17 of its 60 in row 1 and fills every run below from the rest.
  const railsPaired = plan.band.length === 0 || plan.latestList.length === 0;

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
            <div className={ROW}>
              <div className="col-span-3 min-w-0">
                <Briefing facts={facts} />
                {plan.side.length > 0 && (
                  <section aria-labelledby="news-stories-heading" className={cn(TILE, "mt-4")}>
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
                {/* A day too thin to fill the headline list leaves this column ending well above
                    the lead beside it. The column answers with the one thing a thin topic makes a
                    reader ask — who is actually covering it — counted from the same events already
                    on the page. A full day fills the list and never reaches this. */}
                {plan.side.length < SIDE_ROWS && <TodaysPublishers publishers={publishers} />}
              </div>

              <div className={cn("col-span-6 min-w-0", TILE)}>
                {hero && <LeadStory story={hero} />}
                {plan.centre.length > 0 && (
                  <ul className="mt-4 border-t">
                    {plan.centre.map((s) => (
                      <StoryRow key={s.id} story={s} size="md" thumb />
                    ))}
                  </ul>
                )}
              </div>

              <div className="col-span-3 min-w-0 space-y-4">
                <BlindspotRail stories={plan.spots} />
                <MyNewsBias dashboard={dashboard} />
              </div>
            </div>

            {/* Row 2 — stories with thumbnails beside the local module */}
            {plan.band.length > 0 && (
              <div className={cn(ROW, "mt-4")}>
                {/* No measure cap here, unlike the runs below: these rows carry a thumbnail on the
                    right, and a capped list would leave the pictures floating short of the card's
                    own edge. The row runs the full width it was given. */}
                <div className={cn(railsPaired ? "col-span-12" : "col-span-9", "min-w-0", TILE)}>
                  <ul className="-mt-3">
                    {plan.band.map((s) => (
                      <StoryRow key={s.id} story={s} size="md" thumb />
                    ))}
                  </ul>
                </div>
                {!railsPaired && (
                  <div className={cn("col-span-3 min-w-0", TILE)}>
                    <LocalNews />
                  </div>
                )}
              </div>
            )}

            {plan.sections[0] && <TopicSection {...plan.sections[0]} />}

            {/* Latest stories beside the topic index */}
            {plan.latestList.length > 0 && (
              <div className={cn(ROW, "mt-4")}>
                <div className={cn(railsPaired ? "col-span-12" : "col-span-9", "min-w-0", TILE)}>
                  <h2 className={cn(SECTION_TITLE, "mb-1")}>{t("home.latest.title")}</h2>
                  <ul className={cn(railsPaired && "max-w-4xl")}>
                    {plan.latestList.map((s) => (
                      <StoryRow key={s.id} story={s} size="sm" />
                    ))}
                  </ul>
                </div>
                {!railsPaired && (
                  <div className={cn("col-span-3 min-w-0", TILE)}>
                    <SimilarTopics topics={rail} active={topic} onSelect={setTopic} />
                  </div>
                )}
              </div>
            )}

            {plan.sections[1] && <TopicSection {...plan.sections[1]} />}

            {/* Closing run */}
            {plan.closing.length > 0 && (
              <section aria-labelledby="latest-news-heading" className={cn(TILE, "mt-4")}>
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
            )}

            {/* The two reader modules, paired across the width when neither run kept its rail. */}
            {railsPaired && (
              <div className={cn(ROW, "mt-4")}>
                <div className={cn(rail.length > 0 ? "col-span-6" : "col-span-12", "min-w-0", TILE)}>
                  <LocalNews />
                </div>
                {rail.length > 0 && (
                  <div className={cn("col-span-6 min-w-0", TILE)}>
                    <SimilarTopics topics={rail} active={topic} onSelect={setTopic} />
                    {/* The closing run carries this link on a full day. When the day was too thin
                        for that run to render, the way out to the whole catalog goes here rather
                        than off the page. */}
                    <Button asChild variant="outline" size="sm" className={cn(OUTLINE_BTN, "mt-5 w-full")}>
                      <Link href="/stories?sort=latest">{t("home.moreStories")}</Link>
                    </Button>
                  </div>
                )}
              </div>
            )}
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
    <div className="border-b bg-card">
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
    <section aria-labelledby="briefing-heading" className={TILE}>
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

/** The lead: picture (or the shared fallback), the headline at display scale, the labelled strip. */
function LeadStory({ story }: { story: Story }) {
  const { t, formatCompact } = useTranslation();
  const publisherCount = story.publisherCount ?? story.publishers?.length ?? null;

  return (
    <article className="group">
      <Link href={`/stories/${story.id}`} className="block rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
        <CardImage
          src={story.image}
          alt={story.title}
          priority
          aspect="aspect-[16/9]"
          className="rounded-md"
        />
        {/* Unconditional: the fallback carries no facts of its own, so the strip below it is never
            a repeat — every lead reads the same whether or not a picture was published. */}
        <div className="mt-3">
          <BiasStrip distribution={story.distribution} labels />
        </div>
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
        <CardImage src={story.image} alt="" aspect="aspect-[16/9]" className="rounded-md" />
        <div className="mt-2">
          <BiasStrip distribution={story.distribution} labels />
        </div>
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
    <section aria-labelledby="blindspot-rail-heading" className={TILE}>
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
    <section aria-labelledby="my-bias-heading" className={TILE}>
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

/**
 * "Publishers covering today" — who is carrying the events on this page, ranked by how many of
 * them they appear in.
 *
 * Counted from the payload the page is already built from (`publisherStats`), never a curated
 * masthead: the product does not claim a newsroom it cannot see in the corpus. Under a topic
 * filter it counts that topic's events, which is the honest reading of the same question. There is
 * no follow control because there is no follow contract in the engine.
 */
function TodaysPublishers({ publishers }: { publishers: PublisherCount[] }) {
  const { t, formatCompact } = useTranslation();
  const top = publishers.slice(0, PUBLISHER_ROWS);
  if (top.length === 0) return null;
  const max = top[0]?.stories || 1;
  return (
    <section aria-labelledby="publishers-heading" className={cn(TILE, "mt-4")}>
      <h2 id="publishers-heading" className={cn(SECTION_TITLE, "mb-3")}>
        {t("home.publishers.title")}
      </h2>
      <ul className="space-y-3">
        {top.map((entry) => (
          <li key={entry.publisher}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-[13px] font-medium">{entry.publisher}</span>
              <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                {t("home.publishers.count", { n: formatCompact(entry.stories) })}
              </span>
            </div>
            {/* Presentational only — the count beside it carries the same value for screen readers. */}
            <div aria-hidden className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-foreground/30"
                style={{ width: `${Math.max(4, (entry.stories / max) * 100)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
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

/**
 * A topic section: "{Topic} news" — the latest event as a big card, its coverage gaps beside it.
 *
 * The gap grid is two-up when the topic has two cards to show and one-up when it has one, so the
 * single-card topic fills the column it was given instead of sitting in the left half of a
 * two-column row with a hole beside it. A topic never has zero: `groupByTopic(minStories: 2)` only
 * files a topic with at least two events, and the lead is one of them.
 *
 * This row keeps grid's default stretch, unlike the page rows above: `border-l` is a real divider
 * between two halves of one module, and a divider that stops short of the taller half is a bug.
 */
function TopicSection({ group, lead, gaps }: { group: TopicGroup; lead: Story; gaps: Story[] }) {
  const { t } = useTranslation();
  const href = `/stories?topic=${encodeURIComponent(group.topic)}`;
  const headingId = `topic-${group.topic.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section aria-labelledby={headingId} className={cn(TILE, "mt-4")}>
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
              <CardImage src={lead.image} alt={lead.title} aspect="aspect-[16/9]" className="rounded-md" />
              <div className="mt-3">
                <BiasStrip distribution={lead.distribution} labels />
              </div>
              <h3 className="mt-3 text-balance text-[26px] font-bold leading-[1.15] tracking-tight transition-colors group-hover:text-primary">
                {lead.title}
              </h3>
            </Link>
          </article>
        </div>
        <div className="col-span-5 min-w-0 border-l pl-6">
          <p className={cn(LABEL, "mb-3")}>{t("home.topic.blindspots", { topic: group.topic })}</p>
          <ul className={cn("grid gap-4", gaps.length > 1 ? "grid-cols-2" : "grid-cols-1")}>
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
