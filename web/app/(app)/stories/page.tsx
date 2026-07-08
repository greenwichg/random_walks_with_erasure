"use client";

import { Newspaper } from "lucide-react";
import { StoryBrowser } from "@/components/stories/story-browser";

export default function StoriesPage() {
  return (
    <StoryBrowser
      title="Stories"
      icon={Newspaper}
      defaultSort="top"
      description="One event, every viewpoint — the same story clustered across left, center, and right publishers."
      emptyDescription="Stories cluster the live news catalog into events. Once enough articles across publishers cover the same event, they appear here."
    />
  );
}
