import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { FeedbackAction, Recommendation } from "@ih/core/domain/types";
import { feedbackArticleId, feedbackWire } from "@ih/core/api/services";
import { countryName } from "@ih/core/logic/countries";
import { partitionByCountryMatch } from "@ih/core/logic/country-partition";
import { presentRecommendation } from "@ih/core/logic/rec-presentation";

import { PageTitle, Screen } from "@/components/layout/screen";
import { RecommendationCard } from "@/components/recommendations/recommendation-card";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs } from "@/components/ui/tabs";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { track } from "@/lib/analytics";
import {
  useFeedback,
  useOpenRecommendation,
  useRecommendationFeedback,
  useRecommendations,
  useRemoveFeedback,
  useSettings,
} from "@/lib/hooks";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

type Filter = "all" | Recommendation["strategy"];

/** The consequence line each Tier-2 action earns — the visible half of the feedback loop. */
const VOCAB_CONSEQUENCE: Partial<Record<FeedbackAction, string>> = {
  "another-viewpoint": "rec.consequence.anotherViewpoint",
  "already-know": "rec.consequence.alreadyKnow",
  "too-repetitive": "rec.consequence.tooRepetitive",
  "fewer-from-source": "rec.consequence.fewerFromSource",
  "more-topic": "rec.consequence.moreTopic",
};

type Consequence = { articleId: string; cardId: string; action: FeedbackAction; publisher: string; topic: string };

/**
 * For You — the recommendation feed: strategy tabs, the consequence strips (each with an undo),
 * and the cards, partitioned country-first with the "coverage ends here" divider where the
 * selected country's supply ran out. Everything with a decision in it is `@ih/core`.
 */
export default function RecommendationsScreen() {
  const { data, isLoading, isError, refetch } = useRecommendations();
  const { t, lang } = useTranslation();
  const { palette } = useTheme();
  const feedback = useFeedback();
  const openRec = useOpenRecommendation();
  const [filter, setFilter] = React.useState<Filter>("all");
  const [dismissed, setDismissed] = React.useState<Set<string>>(new Set());

  const { data: feedbackLog } = useRecommendationFeedback();
  const persistedIgnored = React.useMemo(
    () => new Set((feedbackLog ?? []).filter((f) => f.feedback === "ignore").map((f) => f.articleId)),
    [feedbackLog],
  );

  const visible = (data ?? [])
    .filter((r) => (filter === "all" ? true : r.strategy === filter))
    .filter((r) => !dismissed.has(r.article.id) && !persistedIgnored.has(r.article.id) && !persistedIgnored.has(feedbackArticleId(r.article)));

  const { data: settings } = useSettings();
  const selectedCountry = settings?.recommendationCountry ?? null;
  const { ordered, firstBackfill } = React.useMemo(() => partitionByCountryMatch(visible), [visible]);

  React.useEffect(() => {
    if (data && data.length) track("recommendations_viewed", { count: data.length });
  }, [data]);

  const removeFeedback = useRemoveFeedback();
  const [consequences, setConsequences] = React.useState<Consequence[]>([]);

  const handleAction = (rec: Recommendation, action: FeedbackAction) => {
    track("recommendation_feedback", { action });
    const wireId = feedbackArticleId(rec.article);
    feedback.mutate({ articleId: wireId, action });
    if (VOCAB_CONSEQUENCE[action]) {
      setConsequences((prev) => [
        { articleId: wireId, cardId: rec.article.id, action, publisher: rec.article.publisher, topic: rec.article.topic },
        ...prev.filter((c) => !(c.articleId === wireId && c.action === action)),
      ]);
    }
  };

  const undoConsequence = (c: Consequence) => {
    const wire = feedbackWire(c.action);
    if (wire) removeFeedback.mutate({ articleId: c.articleId, feedback: wire });
    setConsequences((prev) => prev.filter((x) => !(x.articleId === c.articleId && x.action === c.action)));
    setDismissed((prev) => {
      const next = new Set(prev);
      next.delete(c.cardId);
      return next;
    });
  };

  const handleOpen = (rec: Recommendation) => {
    track("recommendation_opened", { strategy: rec.strategy, crossCutting: rec.crossCutting });
    openRec.mutate({ articleId: rec.article.id, crossCutting: rec.crossCutting });
  };
  // Kept for parity with the feed's story-card suppression; the continuation strip that drives
  // it is not on the phone, so nothing is withheld.
  void presentRecommendation;

  return (
    <Screen>
      <PageTitle title={t("rec.title")} subtitle={t("rec.subtitle")} />

      <Tabs
        value={filter}
        onChange={setFilter}
        style={{ marginBottom: 24 }}
        items={[
          { value: "all", label: t("rec.filter.all"), icon: "sparkles" },
          { value: "rwe-b", label: t("rec.strategy.rwe-b"), icon: "route" },
          { value: "adaptive", label: t("rec.strategy.adaptive"), icon: "wand" },
          { value: "rwe-d", label: t("rec.strategy.rwe-d"), icon: "compass" },
        ]}
      />

      {consequences.length > 0 && (
        <View style={{ gap: 8, marginBottom: 16 }}>
          {consequences.map((c) => (
            <View
              key={`${c.articleId}:${c.action}`}
              accessibilityRole="alert"
              style={[styles.consequence, { borderColor: alpha(palette.primary, 0.25), backgroundColor: alpha(palette.primary, 0.04) }]}
            >
              <Txt size={14} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
                {t(VOCAB_CONSEQUENCE[c.action]!, { publisher: c.publisher, topic: c.topic || t("rec.fb.thisTopic") })}
              </Txt>
              <Pressable accessibilityRole="button" onPress={() => undoConsequence(c)} hitSlop={6}>
                <Txt size={14} weight="500" color={palette.primary}>
                  {t("rec.consequence.undo")}
                </Txt>
              </Pressable>
            </View>
          ))}
        </View>
      )}

      {isLoading && (
        <View style={{ gap: 20 }} accessibilityElementsHidden>
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} height={288} />
          ))}
        </View>
      )}
      {isError && <ErrorState onRetry={() => void refetch()} />}

      {data && data.length === 0 && (
        <EmptyState
          icon="sparkles"
          title={t("rec.firstRun.title")}
          description={t("rec.firstRun.body")}
          style={{ marginTop: 16 }}
          action={<Button onPress={() => navigate("/discover")}>{t("rec.empty.cta")}</Button>}
        />
      )}

      {data && data.length > 0 && visible.length === 0 && (
        <EmptyState
          icon="sparkles"
          title={t("rec.empty.title")}
          description={t("rec.empty.body")}
          style={{ marginTop: 16 }}
          action={<Button onPress={() => navigate("/discover")}>{t("rec.empty.cta")}</Button>}
        />
      )}

      <View style={{ gap: 20 }}>
        {ordered.map((rec, i) => (
          <React.Fragment key={rec.article.id}>
            {i === firstBackfill && (
              <View style={styles.divider} accessibilityRole="none">
                <View style={[styles.rule, { backgroundColor: palette.border }]} />
                <Txt size={12} muted align="center" style={{ flexShrink: 1 }}>
                  {selectedCountry ? t("rec.backfill.after", { country: countryName(selectedCountry, lang) }) : t("rec.backfill.generic")}
                </Txt>
                <View style={[styles.rule, { backgroundColor: palette.border }]} />
              </View>
            )}
            <RecommendationCard
              rec={rec}
              onAction={(action) => handleAction(rec, action)}
              onOpen={() => handleOpen(rec)}
              onDismiss={() => setDismissed((prev) => new Set(prev).add(rec.article.id))}
            />
          </React.Fragment>
        ))}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  consequence: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, borderWidth: 1, borderRadius: radius.lg, paddingHorizontal: 12, paddingVertical: 8 },
  divider: { flexDirection: "row", alignItems: "center", gap: 12, paddingTop: 8 },
  rule: { flex: 1, height: StyleSheet.hairlineWidth },
});
