"use client";

import * as React from "react";
import Link from "next/link";
import { Check, ChevronDown, MoreVertical, Share2 } from "lucide-react";
import type { Story } from "@ih/core/domain/types";
import { interestForTopic, isFollowedInterest, toggleInterest } from "@ih/core/logic/interests";
import { useSettings, useUpdateSettings } from "@/hooks/use-data";
import { CardImage } from "@/components/shared/card-image";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * SIMILAR STORIES — the story page's "what else is this like" rail.
 *
 * It replaces the vertical "More stories" list that used to close the page. What it renders is
 * decided entirely upstream: the engine scores every story in the catalog against this one with the
 * clusterer's own IDF-weighted profile overlap and returns the ones above its same-event floor,
 * ranked (`/api/stories/{id}/similar`). The page hands that array straight here.
 *
 * So this component holds no notion of "similar" of its own, and must not acquire one. In
 * particular it does not pad: an array shorter than {@link MAX_CARDS} means the catalog held
 * nothing closer, and an EMPTY array means nothing qualified at all — which renders as no section,
 * not as a section filled with the day's top stories. That padding was the defect the rail was
 * reported for, and it put a Venezuelan oil deal beside a Supreme Court ruling about a ballroom.
 *
 * A rail rather than a list because the selection is a browse surface, not a ranking: the reader
 * is meant to skim sideways and pick, and the horizontal form says that where a numbered column
 * implied an order the data does not carry. It also let the display cap rise from 4 to
 * {@link MAX_CARDS} at ZERO extra network cost — the page already fetched up to eleven candidates
 * and discarded seven of them.
 *
 * Collapsible because it is the last thing on a long page and the reader who wants the coverage
 * list should be able to fold this away; open by default, since a collapsed rail on first visit
 * would hide the only "where next" the page offers.
 */

/** How many cards the rail shows. Bounded by what the page's existing queries already return
 *  (topic limit 5 + top limit 6, minus this story), so raising it costs no additional request. */
export const MAX_CARDS = 10;

export function SimilarStories({ stories }: { stories: Story[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(true);
  const shown = stories.slice(0, MAX_CARDS);
  if (shown.length === 0) return null;

  return (
    <section aria-labelledby="similar-stories-heading" className="border-t pt-2">
      {/* The heading IS the control (the accordion pattern): a button inside the h2, so assistive
          tech announces the section by name and its expanded state in one stop. */}
      <h2 id="similar-stories-heading">
        <button
          type="button"
          aria-expanded={open}
          aria-controls="similar-stories-rail"
          onClick={() => setOpen((v) => !v)}
          className={cn(
            "flex w-full items-center justify-between gap-3 rounded py-4 text-left",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <span className="text-[22px] font-bold leading-tight tracking-tight sm:text-2xl">
            {t("story.related")}
          </span>
          <ChevronDown
            className={cn(
              "h-6 w-6 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
            aria-hidden
          />
        </button>
      </h2>

      {open && (
        <ul
          id="similar-stories-rail"
          // Edge-to-edge on a phone: the negative margin and matching padding let the first card
          // sit flush with the page gutter and the last one scroll past it, which is what makes
          // the next card PEEK instead of being clipped against a hard container edge.
          //
          // `scroll-px-4` is load-bearing, not decoration. A snapport is the padding box inset by
          // scroll-padding — NOT the content box — so with mandatory snapping and no scroll-padding
          // the browser snaps the first card's edge to the padding box and swallows the gutter on
          // load: measured cardLeft 0 against a heading at 16. Matching scroll-padding to the
          // padding is what keeps every snap position on the page's own gutter.
          className={cn(
            "-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-4 scroll-px-4",
            "sm:mx-0 sm:scroll-px-0 sm:px-0",
            "[scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
          )}
        >
          {shown.map((story) => (
            <SimilarStoryCard key={story.id} story={story} />
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * One card: a square mark on the left, the headline and its dateline on the right, and a footer
 * bar carrying the counted source total and the card's own menu.
 *
 * The image column is deliberately square and fixed-width, so a row of cards forms a clean
 * left-hand column of marks and the headline gets the same measure on every card whatever its
 * length. A story with no art gets the shared newspaper fallback through {@link CardImage} —
 * the same slot every other card in the app fronts, so the rail can never open a hole.
 */
function SimilarStoryCard({ story }: { story: Story }) {
  const { t, formatCompact, timeAgo } = useTranslation();

  return (
    <li className="w-[300px] shrink-0 snap-start sm:w-[340px]">
      <div className="flex h-full flex-col overflow-hidden rounded-lg border bg-card shadow-soft">
        <Link
          href={`/stories/${story.id}`}
          className="group flex focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <CardImage
            src={story.image}
            alt=""
            aspect="aspect-square"
            className="w-28 shrink-0 rounded-none sm:w-32"
          />
          <div className="flex min-w-0 flex-1 flex-col p-3">
            <h3 className="line-clamp-4 text-[15px] font-bold leading-[1.25] tracking-tight transition-colors group-hover:text-primary">
              {story.title}
            </h3>
            {/* Pushed to the bottom of the column so the dateline sits on the image's baseline
                however many lines the headline runs to. */}
            <span className="mt-auto pt-2 text-[12px] text-muted-foreground">
              {timeAgo(story.updatedAt)}
            </span>
          </div>
        </Link>

        <div className="flex items-center justify-between gap-2 border-t px-3 py-2">
          <span className="text-[13px] font-semibold">
            {t("storyCard.sources", { n: formatCompact(story.totalCoverage) })}
          </span>
          <StoryCardMenu story={story} />
        </div>
      </div>
    </li>
  );
}

/**
 * The card's overflow menu — and only what the product can actually DO to a story from here.
 *
 * There is no save-story and no hide-story contract (saving is keyed on an ARTICLE's canonical
 * URL; a story is a cluster), so neither is offered: a menu item that cannot be honoured is worse
 * than a shorter menu. What is left is real on both counts —
 *
 *   Share   the story's own URL, through the platform sheet where there is one and the clipboard
 *           otherwise. Frontend only, exactly as the story page's own ShareButton works.
 *   Follow  the story's topic, when it maps to one of the eight Interest Intensity sliders. It
 *           writes through `useUpdateSettings` — the same mutation FollowButton and the settings
 *           page use — so a follow made here shows up in both. The item is absent for a topic
 *           outside the eight, because there would be nothing to nudge (@ih/core/logic/interests).
 */
function StoryCardMenu({ story }: { story: Story }) {
  const { t } = useTranslation();
  const settings = useSettings();
  const update = useUpdateSettings();
  const [copied, setCopied] = React.useState(false);

  const key = story.topic ? interestForTopic(story.topic) : null;
  const following = key ? isFollowedInterest(settings.data?.interests, key) : false;

  const share = async () => {
    const url = `${window.location.origin}/stories/${story.id}`;
    try {
      if (navigator.share) {
        await navigator.share({ title: story.title, url });
        return;
      }
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* sheet dismissed / clipboard denied — nothing to report */
    }
  };

  const onFollow = () => {
    const current = settings.data;
    if (!current || !key) return;
    update.mutate({ interests: toggleInterest(current.interests, key) });
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={t("story.similar.options")}
        className={cn(
          "-mr-1 grid h-8 w-8 shrink-0 place-items-center rounded-full text-muted-foreground",
          "transition-colors hover:bg-accent hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <MoreVertical className="h-4 w-4" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={share}>
          {copied ? <Check className="h-4 w-4" aria-hidden /> : <Share2 className="h-4 w-4" aria-hidden />}
          {copied ? t("common.copied") : t("story.share")}
        </DropdownMenuItem>
        {key && story.topic && (
          <DropdownMenuItem onClick={onFollow}>
            {following ? <Check className="h-4 w-4" aria-hidden /> : null}
            {t(following ? "story.similar.unfollowTopic" : "story.similar.followTopic", {
              topic: story.topic,
            })}
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
