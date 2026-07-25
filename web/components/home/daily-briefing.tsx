"use client";

import Link from "next/link";
import { EyeOff, Newspaper, ScanSearch } from "lucide-react";
import type { BriefingFacts } from "@/lib/home";
import { useTranslation } from "@/lib/i18n";

/**
 * "Today's briefing" — the page's opening statement, built from COUNTED facts about the coverage
 * actually loaded: how many events, across how many publishers, and how many of them the Story
 * Service flagged as thin on one side.
 *
 * Explicitly NOT an AI-written summary. The product has no daily-briefing generator (the AI Coach
 * is conversational and grounded in the reader's own report), and inventing prose here would be the
 * one thing this product refuses to do — assert something it cannot show. Counted facts carry the
 * same orienting value and are verifiable against the sections below.
 */
export function DailyBriefing({ facts }: { facts: BriefingFacts }) {
  const { t, timeAgo, formatCompact } = useTranslation();

  return (
    <section
      aria-labelledby="briefing-heading"
      className="rounded-lg border bg-card p-5 shadow-soft"
    >
      {/* Neutral kicker — accent colour is reserved for interactive state (the analyze link). */}
      <p className="text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground">
        {t("home.briefing.eyebrow")}
      </p>

      <h2 id="briefing-heading" className="mt-1.5 text-xl font-semibold tracking-tight text-balance">
        {t("home.briefing.headline", {
          stories: formatCompact(facts.storyCount),
          publishers: formatCompact(facts.publisherCount),
        })}
      </h2>

      <p className="mt-2 flex items-center gap-1.5 text-sm text-muted-foreground">
        {facts.blindspotCount > 0 ? (
          <>
            <EyeOff className="h-4 w-4 shrink-0 text-caution" aria-hidden />
            {t("home.briefing.blindspot", { n: formatCompact(facts.blindspotCount) })}
          </>
        ) : (
          <>
            <Newspaper className="h-4 w-4 shrink-0" aria-hidden />
            {t("home.briefing.balanced")}
          </>
        )}
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-3.5 text-xs text-muted-foreground">
        {facts.latestUpdate && <span>{t("home.briefing.updated", { time: timeAgo(facts.latestUpdate) })}</span>}
        <Link
          href="/analyze"
          className="ml-auto inline-flex items-center gap-1.5 font-medium text-primary transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          <ScanSearch className="h-3.5 w-3.5" aria-hidden />
          {t("home.briefing.analyze")}
        </Link>
      </div>
    </section>
  );
}
