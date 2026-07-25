"use client";

import type { TopicGroup } from "@/lib/home";
import { SectionHeader } from "@/components/shared/section-header";
import { StoryListItem } from "@/components/home/story-list-item";
import { useTranslation } from "@/lib/i18n";

/**
 * A repeating category module — one real catalog topic and its deepest coverage.
 *
 * The topic comes from the corpus (see `groupByTopic`), never from a hardcoded section list, so
 * the page never advertises a "Sports" or "Health" desk the catalog can't fill. The lead story
 * carries a thumbnail; the rest are text rows, which keeps a long page scannable and fast.
 */
export function CategorySection({ group, limit = 5 }: { group: TopicGroup; limit?: number }) {
  const { t } = useTranslation();
  const stories = group.stories.slice(0, limit);
  if (stories.length === 0) return null;

  const headingId = `category-${group.topic.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;

  return (
    <section aria-labelledby={headingId}>
      <SectionHeader id={headingId} title={group.topic} eyebrow={t("home.category.eyebrow")} />
      <ul className="divide-y">
        {stories.map((story, i) => (
          <StoryListItem key={story.id} story={story} showImage={i === 0} />
        ))}
      </ul>
    </section>
  );
}
