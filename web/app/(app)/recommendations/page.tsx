"use client";

import * as React from "react";
import Link from "next/link";
import { AnimatePresence } from "framer-motion";
import { Sparkles, Route, Compass, Wand2 } from "lucide-react";
import type { FeedbackAction, Recommendation } from "@ih/core/domain/types";
import { feedbackWire, feedbackArticleId } from "@ih/core/api/services";
import {
  useRecommendations,
  useFeedback,
  useOpenRecommendation,
  useRecommendationFeedback,
  useRemoveFeedback,
  useSettings,
} from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { countryName } from "@ih/core/logic/countries";
import { partitionByCountryMatch } from "@ih/core/logic/country-partition";
import { track } from "@/lib/analytics";
import { PageContainer } from "@/components/layout/page-container";
import { RecommendationCard } from "@/components/recommendations/recommendation-card";
import { ContinuationStrip } from "@/components/shared/continuation-strip";
import { presentRecommendation } from "@ih/core/logic/rec-presentation";
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

/** The Tier-2 vocabulary actions and the consequence line each one earns. The consequence is the
 *  contract: an invisible feedback loop reads as surveillance, a visible one is a control — so
 *  every one of these renders a strip the reader can undo, and Settings lists the active set. */
const VOCAB_CONSEQUENCE: Partial<Record<FeedbackAction, string>> = {
  "another-viewpoint": "rec.consequence.anotherViewpoint",
  "already-know": "rec.consequence.alreadyKnow",
  "too-repetitive": "rec.consequence.tooRepetitive",
  "fewer-from-source": "rec.consequence.fewerFromSource",
  "more-topic": "rec.consequence.moreTopic",
};

/** `articleId` is the WIRE identity the signal was recorded under (feedbackArticleId — the
 *  durable canonical URL where the card has one); `cardId` is the served card's corpus id, which
 *  the session `dismissed` set is keyed by. Undo needs both: remove the signal by the former,
 *  put the card back by the latter. */
type Consequence = {
  articleId: string;
  cardId: string;
  action: FeedbackAction;
  publisher: string;
  topic: string;
};

export default function RecommendationsPage() {
  const { data, isLoading, isError, refetch } = useRecommendations();
  const { t, lang } = useTranslation();
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

  // The story the continuation strip is currently offering, if any. Both it and the engine's
  // story-match card are "here is another outlet on the event you just read", so showing both for
  // ONE story is the same offer twice with different wording — and the strip is the more specific
  // of the two (it names the opposing outlet and the reader's own side). The strip wins; the feed
  // card for that story is withheld while it is up (design §9.1.2).
  const [continuationStory, setContinuationStory] = React.useState<string | null>(null);

  const visible = (data ?? [])
    .filter((r) => (filter === "all" ? true : r.strategy === filter))
    // Persisted rows are keyed by the durable wire identity (URL where the card has one); rows
    // recorded before that change carry the bare corpus id — match both so neither generation
    // of stored ignores resurfaces its card.
    .filter(
      (r) =>
        !dismissed.has(r.article.id) &&
        !persistedIgnored.has(r.article.id) &&
        !persistedIgnored.has(feedbackArticleId(r.article)),
    )
    .filter(
      (r) =>
        continuationStory === null ||
        presentRecommendation(r.explanation).storyId !== continuationStory,
    );

  const { data: settings } = useSettings();
  const selectedCountry = settings?.recommendationCountry ?? null;
  // The engine partitions country-matched items PER STRATEGY, because the blend allocates slots
  // per strategy (Bridging, then Discovery, then For You) and orders each group country-first
  // inside its own budget. The served list is therefore matched-first WITHIN each group, never
  // globally — so a single "coverage ends here" boundary drawn over the raw order lands inside
  // the first group and strands the later groups' country cards below it, which is exactly what
  // it did. Partition the whole visible list here, stably, so every country card sits above the
  // boundary and both parts keep their blend order.
  const { ordered, firstBackfill } = React.useMemo(
    () => partitionByCountryMatch(visible),
    [visible],
  );

  // PA1: the reader saw a recommendation feed — the funnel's "Recommendation Viewed" stage. Fires
  // once when the feed first loads (best-effort).
  React.useEffect(() => {
    if (data && data.length) track("recommendations_viewed", { count: data.length });
  }, [data]);

  const removeFeedback = useRemoveFeedback();
  const [consequences, setConsequences] = React.useState<Consequence[]>([]);

  const handleAction = (rec: Recommendation, action: FeedbackAction) => {
    track("recommendation_feedback", { action }); // PA1 (best-effort)
    // Record under the DURABLE identity (canonical URL where the card has one): positional
    // corpus ids are re-minted every catalog refresh, so a signal keyed by one would silently
    // stop applying — or mis-apply — after the next refresh.
    const wireId = feedbackArticleId(rec.article);
    feedback.mutate({ articleId: wireId, action });
    if (VOCAB_CONSEQUENCE[action]) {
      // visible consequence: one strip per (article, action), newest first, each with an undo
      setConsequences((prev) => [
        {
          articleId: wireId,
          cardId: rec.article.id,
          action,
          publisher: rec.article.publisher,
          topic: rec.article.topic,
        },
        ...prev.filter((c) => !(c.articleId === wireId && c.action === action)),
      ]);
    }
  };

  const undoConsequence = (c: Consequence) => {
    const wire = feedbackWire(c.action);
    if (wire) removeFeedback.mutate({ articleId: c.articleId, feedback: wire });
    setConsequences((prev) => prev.filter((x) => !(x.articleId === c.articleId && x.action === c.action)));
    // the card returns to the feed — the effect and its undo are both immediate and visible
    setDismissed((prev) => {
      const next = new Set(prev);
      next.delete(c.cardId);
      return next;
    });
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

      {/*
        Story Continuation, feed instance (design §9.1.2). Deliberately ABOVE the filter results and
        outside the grid: it is not a recommendation and holds no blend-plan slot — it is the one
        thing on this page that is about what the reader did five minutes ago, so it is neither
        ranked against the feed nor hidden by a filter tab that happens to be selected.

        Unbound (`anchorUrl` omitted): it serves whatever is armed, from wherever the read happened.
        Binding it to a card cannot work here — the recommender excludes what has been read, so the
        card the reader opened is gone from the very next feed.
      */}
      <ContinuationStrip surface="feed" onOfferChange={setContinuationStory} />

      {/* Visible consequences (Tier 2): what the reader's finer-grained feedback just changed
          about their feed, each with an undo. Session-scoped by design — the durable ledger with
          removal lives in Settings → Recommendation feedback. */}
      {consequences.length > 0 && (
        <div className="mb-4 flex flex-col gap-2">
          {consequences.map((c) => (
            <div
              key={`${c.articleId}:${c.action}`}
              role="status"
              className="flex items-center justify-between gap-3 rounded-lg border border-primary/25 bg-primary/[0.04] px-3 py-2 text-sm"
            >
              <span className="min-w-0 truncate">
                {t(VOCAB_CONSEQUENCE[c.action]!, {
                  publisher: c.publisher,
                  topic: c.topic || t("rec.fb.thisTopic"),
                })}
              </span>
              <button
                onClick={() => undoConsequence(c)}
                className="shrink-0 font-medium text-primary hover:underline"
              >
                {t("rec.consequence.undo")}
              </button>
            </div>
          ))}
        </div>
      )}

      {isLoading && (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
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

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
        <AnimatePresence mode="popLayout">
          {ordered.map((rec, i) => (
            <React.Fragment key={rec.article.id}>
              {/* Where the selected country's coverage ran out. The engine partitions the feed
                  (country-matched first, then ordinary recommendations backfilling the slots the
                  country could not fill), so this boundary is a fact about the catalog, not a
                  layout choice — and leaving it unmarked would let a country with a hundred
                  articles look as though it filled the whole feed. */}
              {i === firstBackfill && (
                <div className="col-span-full flex items-center gap-3 pt-2" role="separator">
                  <span className="h-px flex-1 bg-border" />
                  <span className="text-xs text-muted-foreground">
                    {selectedCountry
                      ? t("rec.backfill.after", { country: countryName(selectedCountry, lang) })
                      : t("rec.backfill.generic")}
                  </span>
                  <span className="h-px flex-1 bg-border" />
                </div>
              )}
              <RecommendationCard
                rec={rec}
                index={i}
                onAction={(action) => handleAction(rec, action)}
                onOpen={() => handleOpen(rec)}
                onDismiss={() => setDismissed((prev) => new Set(prev).add(rec.article.id))}
              />
            </React.Fragment>
          ))}
        </AnimatePresence>
      </div>
    </PageContainer>
  );
}
