"use client";

import * as React from "react";
import { AnimatePresence } from "framer-motion";
import { Sparkles, Route, Compass, Wand2 } from "lucide-react";
import type { FeedbackAction, Recommendation } from "@/types/domain";
import { useRecommendations, useFeedback, useOpenRecommendation } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { PageContainer } from "@/components/layout/page-container";
import { RecommendationCard } from "@/components/recommendations/recommendation-card";
import { ErrorState, EmptyState } from "@/components/shared/states";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
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

  const visible = (data ?? [])
    .filter((r) => (filter === "all" ? true : r.strategy === filter))
    .filter((r) => !dismissed.has(r.article.id));

  const handleAction = (articleId: string, action: FeedbackAction) => {
    feedback.mutate({ articleId, action });
  };

  const handleOpen = (rec: Recommendation) => {
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
        <TabsList>
          {FILTERS.map((f) => (
            <TabsTrigger key={f.value} value={f.value}>
              <f.icon className="h-3.5 w-3.5" />
              {f.value === "all" ? t("rec.filter.all") : t(`rec.strategy.${f.value}`)}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {isLoading && (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-72 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && visible.length === 0 && (
        <EmptyState
          icon={Sparkles}
          title={t("rec.empty.title")}
          description={t("rec.empty.body")}
          className="mt-4"
        />
      )}

      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
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
