import { useLocalSearchParams } from "expo-router";
import * as React from "react";

import { Screen } from "@/components/layout/screen";
import { StoryBrowser, type BrowserParams } from "@/components/stories/story-browser";
import { useTranslation } from "@/lib/i18n-context";

/**
 * The Stories page. Reads the optional deep links (`?country=`, `?publisher=`, `?blindspot=`,
 * `?topic=`, `?tag=&from=`, `?sort=`) from the route params — the tab bar's Blind spots and Local
 * destinations are this screen with a param set — so the filter arrives already applied.
 */
export default function StoriesScreen() {
  const { t } = useTranslation();
  const params = useLocalSearchParams<Record<keyof BrowserParams, string>>();
  return (
    <Screen>
      <StoryBrowser
        params={params}
        title={t("stories.title")}
        icon="newspaper"
        defaultSort="top"
        description={t("stories.subtitle")}
        emptyDescription={t("stories.empty.body")}
      />
    </Screen>
  );
}
