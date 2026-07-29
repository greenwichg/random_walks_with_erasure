"use client";

import * as React from "react";
import Link from "next/link";
import { AnimatePresence } from "framer-motion";
import { Sparkles, Route, Compass, Wand2 } from "lucide-react";
import type { FeedbackAction, Recommendation } from "@/types/domain";
import {
  useRecommendations,
  useFeedback,
  useOpenRecommendation,
  useRecommendationFeedback,
} from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { track } from "@/lib/analytics";
import { PageContainer } from "@/components/layout/page-container";
import { RecommendationCard } from "@/components/recommendations/recommendation-card";
import { ErrorState, EmptyState } from "@/components/shared/states";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

type Filter = "all" | Recommendation["strategy"];

// Label comes from the catalog at render time: `rec.filter.all` for All, else `rec.strategy.<value>`.
const FILTERS: { value: Filter; icon: React.ElementType }[] = [
  { value: "all", icon: Sparkles },
  { value: "rwe-b", icon: Route },
  { value: "adaptive", icon: Wand2 },
  { value: "rwe-d", icon: Compass },
];

export default function RecommendationsPage() {
  const { data, isLoading, isError, refetch } = useRecommendations();
  const { t } = useTranslation();
  const feedback = useFeedback();
  const openRec = useOpenRecommendation();
  const [filter, setFilter] = React.useState<Filter>("all");
  const [dismissed, setDismissed] = React.useState<Set<string>>(new Set());

  // Persisted "ignore" feedback keeps a dismissed card gone across reloads. The engine still serves
  // the same feed in the same order (this is a presentation filter, not ranking); it merely hides
  // what the reader already ignored — the same effect as the session-local `dismissed` set, seeded
  // from the backend so it survives a reload.
  const { data: feedbackLog } = useRecommendationFeedback();
  const persistedIgnored = React.useMemo(
    () => new Set((feedbackLog ?? []).filter((f) => f.feedback === "ignore").map((f) => f.articleId)),
    [feedbackLog],
  );

  const visible = (data ?? [])
    .filter((r) => (filter === "all" ? true : r.strategy === filter))
    .filter((r) => !dismissed.has(r.article.id) && !persistedIgnored.has(r.article.id));

  // PA1: the reader saw a recommendation feed — the funnel's "Recommendation Viewed" stage. Fires
  // once when the feed first loads (best-effort).
  React.useEffect(() => {
    if (data && data.length) track("recommendations_viewed", { count: data.length });
  }, [data]);

  const handleAction = (articleId: string, action: FeedbackAction) => {
    track("recommendation_feedback", { action }); // PA1 (best-effort)
    feedback.mutate({ articleId, action });
  };

  const handleOpen = (rec: Recommendation) => {
    // PA1: opening a recommended read — the "Recommendation Accepted" signal (best-effort).
    track("recommendation_opened", { strategy: rec.strategy, crossCutting: rec.crossCutting });
    // Records reception of a recommended read; the hook refreshes the report so Open-Mindedness
    // (driven by cross-cutting reception) updates automatically.
    openRec.mutate({ articleId: rec.article.id, crossCutting: rec.crossCutting });
  };

  return (
    <PageContainer>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("rec.title")}</h1>
          <p className="mt-1 max-w-xl text-sm text-muted-foreground">{t("rec.subtitle")}</p>
        </div>
      </div>

      <Tabs value={filter} onValueChange={(v) => setFilter(v as Filter)} className="mb-6">
        {/* MB1: the filter strip can exceed a phone's width (4 tabs + icons, longer in some locales) —
            let it scroll horizontally and align left instead of overflowing the page. */}
        <TabsList className="max-w-full justify-start overflow-x-auto">
          {FILTERS.map((f) => (
            <TabsTrigger key={f.value} value={f.value}>
              <f.icon className="h-3.5 w-3.5" />
              {f.value === "all" ? t("rec.filter.all") : t(`rec.strategy.${f.value}`)}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {isLoading && (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-72 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {/* Two different empty states, because they are two different situations and the existing
          copy ("You're all caught up · no more recommendations in this filter") is actively wrong
          for the first one. `data.length === 0` means the API returned nothing at all — a reader
          with no reading history, for whom the engine now declines to invent a feed rather than
          serving the fallback reader's. `visible.length === 0` with a non-empty `data` is just the
          tab filter. */}
      {data && data.length === 0 && (
        <EmptyState
          icon={Sparkles}
          title={t("rec.firstRun.title")}
          description={t("rec.firstRun.body")}
          className="mt-4"
          action={
            <Button asChild>
              <Link href="/discover">{t("rec.empty.cta")}</Link>
            </Button>
          }
        />
      )}

      {data && data.length > 0 && visible.length === 0 && (
        <EmptyState
          icon={Sparkles}
          title={t("rec.empty.title")}
          description={t("rec.empty.body")}
          className="mt-4"
          action={
            <Button asChild>
              <Link href="/discover">{t("rec.empty.cta")}</Link>
            </Button>
          }
        />
      )}

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
        <AnimatePresence mode="popLayout">
          {visible.map((rec, i) => (
            <RecommendationCard
              key={rec.article.id}
              rec={rec}
              index={i}
              onAction={(action) => handleAction(rec.article.id, action)}
              onOpen={() => handleOpen(rec)}
              onDismiss={() => setDismissed((prev) => new Set(prev).add(rec.article.id))}
            />
          ))}
        </AnimatePresence>
      </div>
    </PageContainer>
  );
}
