"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { Newspaper } from "lucide-react";
import { StoryBrowser } from "@/components/stories/story-browser";
import { useTranslation } from "@/lib/i18n";

/** Reads the optional ?country= / ?publisher= deep links (the home "From your places" rail and
 *  the publisher profile page), so the filter arrives already applied. useSearchParams needs the
 *  Suspense boundary. */
function StoriesInner() {
  const { t } = useTranslation();
  const params = useSearchParams();
  const country = params.get("country") ?? undefined;
  const publisher = params.get("publisher") ?? undefined;
  return (
    <StoryBrowser
      title={t("stories.title")}
      icon={Newspaper}
      defaultSort="top"
      initialCountry={country}
      initialPublisher={publisher}
      description={t("stories.subtitle")}
      emptyDescription={t("stories.empty.body")}
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
