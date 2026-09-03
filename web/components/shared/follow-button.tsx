"use client";

import { Check, Plus } from "lucide-react";
import type { Settings } from "@ih/core/domain/types";
import {
  interestForTopic,
  isFollowedInterest,
  toggleInterest,
} from "@ih/core/logic/interests";
import { useSettings, useUpdateSettings } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The one follow control — the reference's "+ / ✓" on a topic and its "Follow" on a place, over
 * the two contracts Hidden View actually has:
 *
 *   topic  → `Settings.interests`, the eight Interest Intensity sliders the engine re-ranks with.
 *            A catalog topic outside those eight has nothing to nudge, so this renders NOTHING
 *            rather than a control that would do nothing (see @ih/core/logic/interests).
 *   place  → `Settings.locations`, the followed places Settings > Places already writes.
 *
 * Both write through `useUpdateSettings`, the same mutation the settings page uses, so a follow
 * made here shows up there and vice versa — one state, two surfaces.
 */
export function FollowButton({
  topic,
  place,
  size = "chip",
  className,
}: {
  /** Catalog topic label. Renders nothing unless it maps to an interest slider. */
  topic?: string;
  /** Followed place: an ISO country code and its level. */
  place?: { placeId: string; level: "country" | "region" | "city" };
  /** `chip` sits inside a topic pill; `button` is the standalone Follow button on a section. */
  size?: "chip" | "button";
  className?: string;
}) {
  const { t } = useTranslation();
  const settings = useSettings();
  const update = useUpdateSettings();
  const key = topic ? interestForTopic(topic) : null;

  if (!key && !place) return null;

  const current = settings.data;
  const following = key
    ? isFollowedInterest(current?.interests, key)
    : Boolean(current?.locations?.some((l) => l.placeId === place!.placeId));

  const onToggle = (e: React.MouseEvent) => {
    // The control is often nested inside a link (a topic chip, a section header) — the click is
    // the follow, never the navigation.
    e.preventDefault();
    e.stopPropagation();
    if (!current) return;
    const next: Partial<Settings> = key
      ? { interests: toggleInterest(current.interests, key) }
      : {
          locations: following
            ? (current.locations ?? []).filter((l) => l.placeId !== place!.placeId)
            : [...(current.locations ?? []), place!],
        };
    update.mutate(next);
  };

  const label = following ? t("follow.following") : t("follow.follow");

  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={!current || update.isPending}
      aria-pressed={following}
      aria-label={label}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 font-medium transition-colors disabled:opacity-50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        size === "chip"
          ? "rounded-full text-[11px] text-muted-foreground hover:text-foreground"
          : "touch-target rounded-md border px-3 py-1.5 text-[13px] hover:bg-accent",
        className,
      )}
    >
      {following ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Plus className="h-3.5 w-3.5" aria-hidden />}
      {size === "button" && label}
    </button>
  );
}
