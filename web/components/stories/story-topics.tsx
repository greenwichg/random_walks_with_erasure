"use client";

import * as React from "react";
import type { Story } from "@ih/core/domain/types";
import { ShowAllButton, TopicList } from "@/components/shared/topic-list";
import { useTranslation } from "@/lib/i18n";

/**
 * SIMILAR NEWS TOPICS — what this story is about, and the way out to everything else about it.
 *
 * The list is the engine's (`story_tags`): topics and entities corroborated by the story's own
 * coverage, ranked by corroboration x specificity, with the ones inherited from strongly-related
 * stories marked as such and the story's category marked as the category. Nothing is decided here
 * — this component holds no notion of what a topic is, exactly as the Similar Stories rail beside
 * it holds no notion of what similar means, and for the same reason: a second opinion in the
 * client is a second answer to disagree with.
 *
 * Each row is a LINK to `/stories?tag=…`, not a filter this page applies to itself. The tag is a
 * property of the catalog rather than of this page, so "other stories about Ebola" is a place you
 * can be, share and come back to. The link carries the NORMALISED name and never the display
 * label, so it cannot break on capitalisation.
 *
 * Design is {@link TopicList}, which is the desktop home page's topic index moved out of that file
 * unchanged — the requirement was that this rail look like the one that already exists, and the
 * only way to keep that true past today is for there to be one of it.
 */

/** Rows shown before "Show All". Six is the reference layout's own window, and it is enough to
 *  carry the specific entities without the category tail that ranks below them. */
export const INITIAL_TOPICS = 6;

export function StoryTopics({ story }: { story: Story }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = React.useState(false);
  const tags = story.tags ?? [];
  // Absent, not empty: a story with no corroborated names has nothing to say here, and an empty
  // bordered panel headed "Similar news topics" says something false about the catalog. This is
  // the opposite call from the Similar Stories rail deliberately — that rail's emptiness was
  // ambiguous BECAUSE a threshold could silently empty it everywhere, and a reader who saw a gap
  // could not tell a decision from a fault. Tags have no such threshold: a story either carries
  // corroborated names or it does not, and the category tag means the list is essentially never
  // empty in practice.
  if (tags.length === 0) return null;

  const shown = expanded ? tags : tags.slice(0, INITIAL_TOPICS);
  return (
    <section aria-labelledby="story-topics-heading">
      <h2 id="story-topics-heading" className="text-[22px] font-bold leading-tight tracking-tight sm:text-2xl">
        {t("story.topics")}
      </h2>
      <TopicList
        labelledBy="story-topics-heading"
        items={shown.map((tag) => ({
          value: tag.name,
          label: tag.label,
          href: `/stories?tag=${encodeURIComponent(tag.name)}`,
        }))}
      />
      {tags.length > shown.length && (
        <ShowAllButton onClick={() => setExpanded(true)} label={t("story.topics.showAll")} />
      )}
    </section>
  );
}
