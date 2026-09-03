"use client";

import * as React from "react";
import type { Story } from "@ih/core/domain/types";
import { splitCoverage } from "@ih/core/logic/story-attached";
import { SectionHeader } from "@/components/shared/section-header";
import { InfoTooltip } from "@/components/shared/info-tooltip";
import { BiasBreakdown } from "@/components/stories/breakdown/bias-breakdown";
import { FactualityBreakdown } from "@/components/stories/breakdown/factuality-breakdown";
import { OwnershipBreakdown } from "@/components/stories/breakdown/ownership-breakdown";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * THE story breakdown — one card, three tabs (Bias · Factuality · Ownership), on every story.
 *
 * It replaces two stacked cards that asked two versions of the same question of the same outlets.
 * Stacking cost a rail scroll on desktop and, on a phone, put a second radial a full screen below
 * the first; tabs put the three answers in one place and let the reader pick which one they came
 * for. The tab strip is the only chrome: each tab's body renders bare, so the three can never
 * drift into three differently-framed panels.
 *
 * ONE component, both viewports. There is no desktop copy and no mobile copy — the story page
 * renders this once and the card is fluid, so the phone gets the same three tabs in the flow that
 * the desktop rail gets in its column. Requirement, and the reason the tab bodies take a `coverage`
 * array rather than reading a surface-specific hook.
 *
 * Every tab draws from the story's OWN coverage rows, member-only (`splitCoverage(...).panel` —
 * attached Tier B coverage never voted, M4 containment). Nothing is derived from another tab: an
 * outlet's lean says nothing about who owns it and nothing about how a rater scores its factual
 * reporting, and a tab with no data behind it says so rather than borrowing from its neighbours.
 */

const TABS = ["bias", "factuality", "ownership"] as const;
type Tab = (typeof TABS)[number];

/** Tab labels reuse the section names the product already speaks where one exists — `story.bias`
 *  and `story.ownership` — so the strip cannot end up calling a thing one word here and another
 *  word on the publisher profile. Each tab's info tooltip changes with it: one icon, three
 *  explanations, because the three tabs answer three different questions. */
const TAB_META: Record<Tab, { labelKey: string; infoKey: string }> = {
  bias: { labelKey: "story.bias", infoKey: "story.biasInfo" },
  factuality: { labelKey: "story.factuality", infoKey: "story.factualityInfo" },
  ownership: { labelKey: "story.ownership", infoKey: "story.ownershipInfo" },
};

export function StoryBreakdown({ story }: { story: Story }) {
  const { t } = useTranslation();
  const [tab, setTab] = React.useState<Tab>("bias");
  const coverage = React.useMemo(() => splitCoverage(story.coverage).panel, [story.coverage]);

  return (
    <section aria-labelledby="story-breakdown-heading" className="rounded-lg border bg-card p-4">
      <div className="mb-3 flex items-center gap-1.5">
        <SectionHeader id="story-breakdown-heading" title={t("story.breakdown")} className="mb-0" />
        <InfoTooltip text={t(TAB_META[tab].infoKey)} />
      </div>

      {/* Roving-free tablist: three targets, arrow keys move between them, and the panel below is
          labelled by the active tab so a screen reader hears which breakdown it is reading. */}
      <div role="tablist" aria-label={t("story.breakdown")} className="mb-3 flex gap-1 rounded-md bg-muted p-1">
        {TABS.map((key) => {
          const active = tab === key;
          return (
            <button
              key={key}
              type="button"
              role="tab"
              id={`story-breakdown-tab-${key}`}
              aria-selected={active}
              aria-controls={`story-breakdown-panel-${key}`}
              tabIndex={active ? 0 : -1}
              onClick={() => setTab(key)}
              onKeyDown={(e) => {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                const step = e.key === "ArrowRight" ? 1 : TABS.length - 1;
                const next = TABS[(TABS.indexOf(key) + step) % TABS.length] as Tab;
                setTab(next);
                document.getElementById(`story-breakdown-tab-${next}`)?.focus();
              }}
              className={cn(
                "min-w-0 flex-1 truncate rounded px-2 py-1.5 text-xs font-semibold transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(TAB_META[key].labelKey)}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`story-breakdown-panel-${tab}`}
        aria-labelledby={`story-breakdown-tab-${tab}`}
      >
        {tab === "bias" && <BiasBreakdown distribution={story.distribution} coverage={coverage} />}
        {tab === "factuality" && (
          <FactualityBreakdown coverage={coverage} published={story.factualityPublished} />
        )}
        {tab === "ownership" && <OwnershipBreakdown coverage={coverage} />}
      </div>
    </section>
  );
}
