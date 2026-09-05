import * as React from "react";
import { View } from "react-native";

import type { Story } from "@ih/core/domain/types";

import { ShowAllButton, TopicList } from "@/components/shared/topic-list";
import { useTranslation } from "@/lib/i18n-context";

/** Rows shown before "Show All". */
export const INITIAL_TOPICS = 6;

/**
 * RELATED TOPICS — what this story is about, and the way out to everything else about it. The list
 * is the engine's (`story_tags`), ranked; each row is a LINK to `/stories?tag=…` carrying the
 * NORMALISED name, with `from` naming the story being read so the topic page never answers with
 * nothing but the story the reader just left.
 */
export function StoryTopics({ story }: { story: Story }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = React.useState(false);
  const tags = story.tags ?? [];
  if (tags.length === 0) return null;

  const shown = expanded ? tags : tags.slice(0, INITIAL_TOPICS);
  return (
    <View>
      <TopicList
        items={shown.map((tag) => ({
          value: tag.name,
          label: tag.label,
          href: `/stories?tag=${encodeURIComponent(tag.name)}&from=${encodeURIComponent(story.id)}`,
        }))}
      />
      {tags.length > shown.length && <ShowAllButton onPress={() => setExpanded(true)} label={t("story.topics.showAll")} />}
    </View>
  );
}
