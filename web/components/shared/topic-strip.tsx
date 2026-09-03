"use client";

import * as React from "react";
import Link from "next/link";
import { ChevronLeft, ChevronRight, TrendingUp } from "lucide-react";
import type { TopicCount } from "@ih/core/logic/home";
import { FollowButton } from "@/components/shared/follow-button";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The topic strip under the masthead — the reference layout's row of trending chips, each with
 * its own follow toggle, on both layouts.
 *
 * A chip does two things, which is why the follow control is INSIDE it rather than beside it:
 * the label selects the topic (filtering the page in place when the host passes `onSelect`, else
 * linking to that topic's stories), and the `+`/`✓` follows the interest behind it. Topics with
 * no interest slider behind them show no toggle at all (FollowButton returns null) — so the
 * strip never offers a control that would write nothing.
 *
 * Desktop gets scroll arrows because a pointer has nothing to swipe with; touch just scrolls.
 */
export function TopicStrip({
  topics,
  active,
  onSelect,
  className,
}: {
  topics: TopicCount[];
  /** Selected topic when the host filters in place; null for "all". */
  active?: string | null;
  /** Omit to make the chips links to /stories?topic=… instead of an in-page filter. */
  onSelect?: (topic: string | null) => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const ref = React.useRef<HTMLDivElement>(null);
  const scroll = (dir: 1 | -1) => ref.current?.scrollBy({ left: dir * 320, behavior: "smooth" });
  if (topics.length === 0) return null;

  const CHIP =
    "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  const tone = (on: boolean) =>
    on ? "border-foreground bg-foreground text-background" : "border-border bg-card text-foreground/80 hover:bg-accent";

  return (
    <div className={cn("border-b bg-card", className)}>
      <div className="mx-auto flex h-11 w-full max-w-6xl items-center gap-2 px-4 sm:px-6 lg:px-8">
        <TrendingUp className="hidden h-4 w-4 shrink-0 text-muted-foreground sm:block" aria-hidden />
        <button
          type="button"
          aria-hidden
          tabIndex={-1}
          onClick={() => scroll(-1)}
          className="hidden shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground lg:block"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>

        <div
          ref={ref}
          role={onSelect ? "toolbar" : undefined}
          aria-label={t("home.trending.title")}
          className="flex min-w-0 flex-1 gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {onSelect && (
            <button
              type="button"
              aria-pressed={active == null}
              onClick={() => onSelect(null)}
              className={cn(CHIP, tone(active == null))}
            >
              {t("home.trending.all")}
            </button>
          )}
          {topics.map((entry) => {
            const on = active === entry.topic;
            const label = entry.topic;
            return (
              <span key={entry.topic} className={cn(CHIP, tone(on), "pr-2")}>
                {onSelect ? (
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => onSelect(on ? null : entry.topic)}
                    className="focus-visible:outline-none"
                  >
                    {label}
                  </button>
                ) : (
                  <Link href={`/stories?topic=${encodeURIComponent(entry.topic)}`} className="focus-visible:outline-none">
                    {label}
                  </Link>
                )}
                <FollowButton topic={entry.topic} className={on ? "text-background/80 hover:text-background" : ""} />
              </span>
            );
          })}
        </div>

        <button
          type="button"
          aria-hidden
          tabIndex={-1}
          onClick={() => scroll(1)}
          className="hidden shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground lg:block"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
