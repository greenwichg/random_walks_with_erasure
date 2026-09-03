"use client";

import { useSettings } from "@/hooks/use-data";

/**
 * Where "Local" goes for this reader: the Stories browser scoped to their edition, or — until
 * they have picked one — Stories unscoped, because a Local destination that 404s or silently
 * shows the world would be worse than one that shows everything and lets them narrow it.
 *
 * The edition is `Settings.edition`, falling back to the first followed COUNTRY
 * (`Settings.locations`), which is the same order Local Pulse and the desktop nav resolve. One
 * helper so the masthead, the slide-out menu and the mobile tab bar can never disagree about it.
 */
export function useLocalPlace(): string | null {
  const settings = useSettings();
  return (
    settings.data?.edition ??
    settings.data?.locations?.find((l) => l.level === "country")?.placeId ??
    null
  );
}

export function useLocalHref(): string {
  const place = useLocalPlace();
  return place ? `/stories?country=${encodeURIComponent(place)}` : "/stories";
}
