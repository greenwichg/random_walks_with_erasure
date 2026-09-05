import { useSettings } from "./hooks.ts";

/**
 * Where "Local" goes for this reader: the Stories browser scoped to their edition, or — until
 * they have picked one — Stories unscoped. The edition is `Settings.edition`, falling back to the
 * first followed COUNTRY, the same order Local Pulse resolves. One helper so the menu and the tab
 * bar can never disagree about it.
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
