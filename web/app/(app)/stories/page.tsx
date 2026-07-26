"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { Newspaper } from "lucide-react";
import { StoryBrowser } from "@/components/stories/story-browser";
import { useTranslation } from "@/lib/i18n";

/** Reads the optional ?country= deep link (e.g. the home "From your places" rail), so a place
 *  arrives with its filter already applied. useSearchParams needs the Suspense boundary. */
function StoriesInner() {
  const { t } = useTranslation();
  const country = useSearchParams().get("country") ?? undefined;
  return (
    <StoryBrowser
      title={t("stories.title")}
      icon={Newspaper}
      defaultSort="top"
      initialCountry={country}
      description={t("stories.subtitle")}
      emptyDescription="Stories cluster the live news catalog into events. Once enough articles across publishers cover the same event, they appear here."
    />
  );
}

export default function StoriesPage() {
  return (
    <React.Suspense fallback={null}>
      <StoriesInner />
    </React.Suspense>
  );
}
