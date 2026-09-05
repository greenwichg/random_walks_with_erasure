import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { EmotionShare, Recommendation, RecommendationEvidence, RecommendationExplain } from "@ih/core/domain/types";

import { Skeleton } from "@/components/ui/skeleton";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { useRecommendationExplain } from "@/lib/hooks";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * The "Why?" drawer: the card stays simple; the proof lives one tap away. Renders the
 * per-recommendation evidence from the engine's explain endpoint in scannable sections —
 * Explanation · Story · History · Bridge · Estimated effect · Article metadata. Every line is a real
 * value produced by the recommender; when a value can't be shown the drawer says why. The
 * developer-facing rows (ranks, ids, hyperparameters) are dev-build-only on the web and absent here.
 */
function Section({ title, first = false, children }: { title: string; first?: boolean; children: React.ReactNode }) {
  const { palette } = useTheme();
  return (
    <View style={[styles.section, !first && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: alpha(palette.border, 0.6) }]}>
      <Txt size={10} weight="600" uppercase tracking={0.6} muted style={{ marginBottom: 6 }}>
        {title}
      </Txt>
      <View style={{ gap: 4 }}>{children}</View>
    </View>
  );
}

function Row({ label, value, mono = true }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <View style={styles.row}>
      <Txt size={12} muted>
        {label}
      </Txt>
      {typeof value === "string" ? (
        <Txt size={12} align="right" tabular={mono} style={{ flexShrink: 1, opacity: 0.9 }}>
          {value}
        </Txt>
      ) : (
        value
      )}
    </View>
  );
}

function sideOf(lean: number) {
  if (lean < -0.05) return "filter.left";
  if (lean > 0.05) return "filter.right";
  return "filter.center";
}

function lcr(v: { left: number; center: number; right: number }) {
  return `L ${v.left}% · C ${v.center}% · R ${v.right}%`;
}

function EvidenceSections({ rec, ev, explain }: { rec: Recommendation; ev: RecommendationEvidence; explain: RecommendationExplain }) {
  const { t, localizeExplanation, formatDate } = useTranslation();
  const { palette } = useTheme();
  const fam = ev.outletFamiliarity;
  const explanation = ev.explanation ?? rec.explanation;
  const sev = (explanation?.type === "story_match" ? explanation.evidence : null) as Record<string, unknown> | null;
  const withDate = (publisher: unknown, iso: unknown) => {
    const p = String(publisher ?? "—");
    const d = typeof iso === "string" && iso ? formatDate(iso, { month: "short", day: "numeric" }) : "";
    return d ? `${p} · ${d}` : p;
  };
  const emotionShare = rec.article.emotion;
  const dominant =
    rec.article.dominantEmotion ??
    (emotionShare
      ? (Object.keys(emotionShare) as (keyof EmotionShare)[]).reduce((a, b) => (emotionShare[a] >= emotionShare[b] ? a : b))
      : null);
  void explain;

  return (
    <>
      {explanation && (
        <Section title={t("why.explanation")} first>
          <Txt size={12} lineHeight={16} style={{ opacity: 0.9 }}>
            {localizeExplanation(explanation)}
          </Txt>
        </Section>
      )}

      {sev && (
        <Section title={t("why.story")}>
          <Row label={t("rec.receipt.youRead")} value={withDate(sev.readPublisher, sev.readPublishedAt)} mono={false} />
          <Row label={t("why.thisCoverage")} value={withDate(sev.recPublisher, sev.recPublishedAt)} mono={false} />
          <Row label={t("why.storyReads")} value={String(sev.storyReads ?? 1)} />
          {typeof sev.storyId === "string" && sev.storyId ? (
            <Pressable accessibilityRole="link" onPress={() => navigate(`/stories/${encodeURIComponent(sev.storyId as string)}`)} style={{ alignSelf: "flex-end", paddingTop: 4 }}>
              <Txt size={12} weight="500" color={palette.primary}>
                {t("rec.viewStory")} →
              </Txt>
            </Pressable>
          ) : null}
        </Section>
      )}

      <Section title={t("why.history")}>
        {ev.topicShare && <Row label={ev.topicShare.topic} value={t("why.shareOfReading", { pct: Math.round(ev.topicShare.share * 100) })} />}
        <Row
          label={t("why.prevReads", { publisher: ev.publisher })}
          value={fam.reads === 0 ? t("why.newOutlet") : `${fam.reads} (${Math.round(fam.share * 100)}%)`}
        />
      </Section>

      <Section title={t("why.bridge")}>
        <Row label={t("why.yourPosition")} value={`${t(sideOf(ev.crossCutting.userMeanLean))} (${ev.crossCutting.userMeanLean.toFixed(2)})`} />
        <Row label={t("why.article")} value={`${t(sideOf(ev.crossCutting.articleLean))} (${ev.crossCutting.articleLean.toFixed(2)})`} />
        <Row label={t("why.gap")} value={ev.leanGap.toFixed(2)} />
        <Row label={t("why.crossCutting")} value={ev.crossCutting.value ? t("common.yes") : t("common.no")} mono={false} />
      </Section>

      <Section title={t("why.estEffect")}>
        {ev.viewpointShift ? (
          <>
            <Row label={t("why.current")} value={lcr(ev.viewpointShift.current)} />
            <Row label={t("why.afterReading")} value={lcr(ev.viewpointShift.after)} />
            <Txt size={10} muted lineHeight={14} style={{ paddingTop: 4 }}>
              {t("why.estimatedBasis", { basis: ev.viewpointShift.basis })}
            </Txt>
          </>
        ) : (
          <Txt size={10} muted lineHeight={14}>
            {t("why.noProjection")}
          </Txt>
        )}
      </Section>

      <Section title={t("why.articleMeta")}>
        {rec.article.leanBucket && <Row label={t("why.leaning")} value={t(`filter.${rec.article.leanBucket}`)} mono={false} />}
        <Row label={t("filter.publisher")} value={rec.article.publisher} mono={false} />
        {rec.article.topic ? <Row label={t("why.category")} value={rec.article.topic} mono={false} /> : null}
        {rec.article.register && <Row label={t("why.coverageType")} value={t(`register.${rec.article.register}`)} mono={false} />}
        {dominant && <Row label={t("filter.emotion")} value={t(`emotion.${dominant}`)} mono={false} />}
      </Section>
    </>
  );
}

export function WhyDrawer({ rec, open }: { rec: Recommendation; open: boolean }) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const { data, isLoading, isError } = useRecommendationExplain(open);
  if (!open) return null;
  const ev = data?.recommendations.find((e) => e.articleId === rec.article.id);
  return (
    <View style={[styles.drawer, { borderColor: palette.border, backgroundColor: alpha(palette.muted, 0.2) }]}>
      {isLoading && (
        <View style={{ gap: 8, padding: 12 }} accessibilityElementsHidden>
          <Skeleton height={12} width="66%" />
          <Skeleton height={12} width="50%" />
          <Skeleton height={12} width="60%" />
        </View>
      )}
      {!isLoading && (isError || !data) && (
        <Txt size={12} muted style={{ padding: 12 }}>
          {t("why.unavailable")}
        </Txt>
      )}
      {!isLoading && data && !ev && (
        <Txt size={12} muted style={{ padding: 12 }}>
          {t("why.stale")}
        </Txt>
      )}
      {!isLoading && data && ev && <EvidenceSections rec={rec} ev={ev} explain={data} />}
    </View>
  );
}

const styles = StyleSheet.create({
  drawer: { marginTop: 12, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.lg, overflow: "hidden" },
  section: { paddingHorizontal: 12, paddingVertical: 10 },
  row: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between", gap: 12 },
});
