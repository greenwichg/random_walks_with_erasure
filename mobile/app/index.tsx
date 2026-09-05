import * as React from "react";
import { RefreshControl } from "react-native";

import { HomeMobile } from "@/components/home/home-mobile";
import { STORY_PAGE_SIZE, useHomeModel } from "@/components/home/home-model";
import { Screen } from "@/components/layout/screen";
import { useStories } from "@/lib/hooks";
import { useTheme } from "@/lib/theme";

/**
 * The Hidden View home page — a news-intelligence front page. ONE `/api/stories` request drives
 * the lead, the story lists, the topic sections and the lens tabs (`home-model.ts`); no new
 * backend surface. Pull to refresh is the phone's reload.
 */
export default function HomeScreen() {
  const { palette } = useTheme();
  const stories = useStories({ sort: "top", limit: STORY_PAGE_SIZE });
  const all = React.useMemo(() => stories.data?.stories ?? [], [stories.data]);
  const model = useHomeModel(all);

  return (
    <Screen
      pt={16}
      refreshControl={
        <RefreshControl refreshing={stories.isRefetching} onRefresh={() => void stories.refetch()} tintColor={palette.primary} />
      }
    >
      <HomeMobile model={model} loading={stories.isLoading} error={stories.isError} onRetry={() => void stories.refetch()} />
    </Screen>
  );
}
