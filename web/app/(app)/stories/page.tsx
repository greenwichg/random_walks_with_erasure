"use client";

import { Newspaper } from "lucide-react";
import { StoryBrowser } from "@/components/stories/story-browser";
import { useTranslation } from "@/lib/i18n";

export default function StoriesPage() {
  const { t } = useTranslation();
  return (
    <StoryBrowser
      title={t("stories.title")}
      icon={Newspaper}
      defaultSort="top"
      description={t("stories.subtitle")}
      emptyDescription="Stories cluster the live news catalog into events. Once enough articles across publishers cover the same event, they appear here."
    />
  );
}
