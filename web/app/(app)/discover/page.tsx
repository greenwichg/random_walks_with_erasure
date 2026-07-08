"use client";

import { Compass } from "lucide-react";
import { StoryBrowser } from "@/components/stories/story-browser";

// Discover now displays Story objects (clustered events) from the single Story Service — the same
// service the Stories page consumes — sorted by latest. Opening a story reveals the underlying
// articles, each opening its canonical publisher URL (unchanged Read flow).
export default function DiscoverPage() {
  return (
    <StoryBrowser
      title="Discover"
      icon={Compass}
      defaultSort="latest"
      description="The latest events across every publisher — clustered into stories. Open one to read the real coverage across the spectrum."
      emptyDescription="Discover clusters the live news catalog into the latest events. Once RSS ingestion has run (RWE_RECS_SOURCE=feed), fresh stories appear here."
    />
  );
}
