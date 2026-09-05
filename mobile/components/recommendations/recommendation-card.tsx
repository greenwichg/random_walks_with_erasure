import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { FeedbackAction, Recommendation } from "@ih/core/domain/types";
import { presentRecommendation } from "@ih/core/logic/rec-presentation";

import { LeanBadge, PublisherBadge } from "@/components/shared/article-badges";
import { CardImage } from "@/components/shared/card-image";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { Badge } from "@/components/ui/badge";
import { BottomSheet, SheetItem } from "@/components/ui/bottom-sheet";
import { Card } from "@/components/ui/card";
import { Icon, type IconName } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { WhyDrawer } from "./why-drawer";

const STRATEGY_LABEL_KEY: Record<Recommendation["strategy"], string> = {
  "rwe-b": "rec.strategy.rwe-b",
  "rwe-d": "rec.strategy.rwe-d",
  adaptive: "rec.strategy.adaptive",
  story: "rec.strategy.story",
  emerging: "rec.strategy.emerging",
  blindspot: "rec.strategy.blindspot",
};

/** The Tier-2 feedback vocabulary, surfaced as a "more options" menu beside the vote buttons. */
const VOCAB_MENU: { action: FeedbackAction; icon: IconName; labelKey: string }[] = [
  { action: "another-viewpoint", icon: "arrow-left-right", labelKey: "rec.fb.anotherViewpoint" },
  { action: "already-know", icon: "check-check", labelKey: "rec.fb.alreadyKnow" },
  { action: "too-repetitive", icon: "repeat", labelKey: "rec.fb.tooRepetitive" },
  { action: "fewer-from-source", icon: "minus-circle", labelKey: "rec.fb.fewerFromSource" },
  { action: "more-topic", icon: "plus-circle", labelKey: "rec.fb.moreTopic" },
];

/**
 * A single recommendation with full transparency + the feedback actions. **Every claim on this
 * card comes from `@ih/core`**: `presentRecommendation` decides what the card may say — catalog
 * KEYS and typed parameter refs, never rendered strings — and the shared translator turns them into
 * a sentence, so the explanation reads identically on both platforms.
 */
export function RecommendationCard({
  rec,
  onAction,
  onOpen,
  onDismiss,
}: {
  rec: Recommendation;
  onAction?: (action: FeedbackAction) => void;
  onOpen?: () => void;
  onDismiss?: () => void;
}) {
  const { article } = rec;
  const { t, localizeExplanation, timeAgo, formatDate } = useTranslation();
  const { palette } = useTheme();
  const [readLater, setReadLater] = React.useState(false);
  const [why, setWhy] = React.useState(false);
  const [vote, setVote] = React.useState<"up" | "down" | null>(null);
  const [menu, setMenu] = React.useState(false);
  const pres = presentRecommendation(rec.explanation);
  const historyBacked = !!pres.comparison || rec.explanation?.type === "new_publisher";
  const act = (action: FeedbackAction) => onAction?.(action);
  const when = timeAgo(article.publishedAt);

  return (
    <Card style={styles.card}>
      {/* top row: strategy + dismiss */}
      <View style={styles.top}>
        <View style={styles.inline}>
          <Badge variant={rec.crossCutting ? "right" : "default"} icon={rec.crossCutting ? "route" : "sparkles"}>
            {t(STRATEGY_LABEL_KEY[rec.strategy])}
          </Badge>
          {when ? (
            <Txt size={12} muted>
              {when}
            </Txt>
          ) : null}
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("rec.ignore")}
          hitSlop={8}
          onPress={() => {
            act("ignore");
            onDismiss?.();
          }}
          style={({ pressed }) => [styles.dismiss, pressed && { backgroundColor: palette.muted }]}
        >
          <Icon name="x" size={16} color={palette.mutedForeground} />
        </Pressable>
      </View>

      <CardImage src={article.image} suspect={article.imageSuspect} accessibilityLabel={article.headline} style={{ marginBottom: 12 }} />

      <View style={styles.meta}>
        <PublisherBadge name={article.publisher} lean={article.publisherLean} logo={article.publisherLogo} logoFallbacks={article.publisherLogoFallbacks} />
        {article.topic ? <Txt size={12} weight="500" muted>{`· ${article.topic}`}</Txt> : null}
      </View>
      <Txt display weight="600" size={17} lineHeight={22} tight style={{ marginTop: 6 }}>
        {article.headline}
      </Txt>

      <View style={[styles.inline, { marginTop: 12 }]}>
        <LeanBadge lean={article.lean} bucket={article.leanBucket} />
      </View>

      {/* claim → receipt → proof: the evidence block shows the resolver's evidence. */}
      <View
        style={[
          styles.evidence,
          historyBacked
            ? { borderColor: alpha(palette.primary, 0.25), backgroundColor: alpha(palette.primary, 0.04) }
            : { borderColor: palette.border, backgroundColor: alpha(palette.muted, 0.3) },
        ]}
      >
        {pres.comparison ? (
          <View>
            <View style={styles.evidenceRow}>
              <Icon name="arrow-left-right" size={16} color={palette.primary} style={{ marginTop: 2 }} />
              <Txt size={14} weight="600" lineHeight={19} style={{ flex: 1 }}>
                {t(pres.claimKey ?? "rec.whyThisArticle")}
              </Txt>
            </View>
            <View style={[styles.receipt, { borderColor: palette.border, backgroundColor: alpha(palette.background, 0.6) }]}>
              <View style={styles.receiptRow}>
                <Txt size={10} weight="600" uppercase tracking={0.5} muted>
                  {t("rec.receipt.youRead")}
                </Txt>
                <Txt size={12} weight="500" numberOfLines={1} align="right" style={{ flex: 1 }}>
                  {pres.comparison.readPublisher}
                </Txt>
                {pres.comparison.readAt && (
                  <Txt size={12} muted>
                    ✓ {formatDate(pres.comparison.readAt, { month: "short", day: "numeric" })}
                  </Txt>
                )}
              </View>
              <View style={[styles.receiptRow, { backgroundColor: alpha(palette.primary, 0.05), borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.border }]}>
                <Txt size={10} weight="600" uppercase tracking={0.5} color={palette.primary}>
                  {t(pres.comparison.variant === "follow_up" ? "rec.receipt.updateFrom" : "rec.receipt.compareWith")}
                </Txt>
                <Txt size={12} weight="600" numberOfLines={1} align="right" style={{ flex: 1 }}>
                  {pres.comparison.recPublisher}
                </Txt>
                {pres.comparison.variant === "follow_up" && pres.comparison.hoursAfterRead != null && pres.comparison.hoursAfterRead > 0 && (
                  <Txt size={12} muted>
                    {t("rec.receipt.hoursLater", { n: pres.comparison.hoursAfterRead })}
                  </Txt>
                )}
              </View>
            </View>
            {(pres.storyHref || pres.comparison.variant === "following") && (
              <View style={[styles.receiptFooter]}>
                <Txt size={12} muted>
                  {pres.comparison.variant === "following" ? t("rec.receipt.readsSoFar", { n: pres.comparison.storyReads }) : ""}
                </Txt>
                {pres.storyHref && (
                  <Pressable accessibilityRole="link" onPress={() => navigate(pres.storyHref!)}>
                    <Txt size={12} weight="500" color={palette.primary}>
                      {t("rec.viewStory")} →
                    </Txt>
                  </Pressable>
                )}
              </View>
            )}
          </View>
        ) : pres.reader || pres.contribution ? (
          <View style={styles.evidenceRow}>
            <Icon name="sparkles" size={16} color={palette.primary} style={{ marginTop: 2 }} />
            <View style={{ flex: 1, minWidth: 0 }}>
              {pres.reader && (
                <Txt size={14} weight="600" lineHeight={19}>
                  {t(pres.reader.key, pres.reader.params)}
                </Txt>
              )}
              {pres.contribution && (
                <Txt size={pres.reader ? 12 : 14} weight={pres.reader ? "400" : "600"} muted={!!pres.reader} lineHeight={pres.reader ? 16 : 19} style={pres.reader ? { marginTop: 2 } : undefined}>
                  {t(pres.contribution.key, pres.contribution.params)}
                </Txt>
              )}
            </View>
          </View>
        ) : (
          <View style={styles.evidenceRow}>
            <Icon name="sparkles" size={16} color={palette.primary} style={{ marginTop: 2 }} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <Txt size={10} weight="600" uppercase tracking={0.6} muted style={{ opacity: 0.8 }}>
                {t("rec.whyThisArticle")}
              </Txt>
              <Txt size={14} muted style={{ marginTop: 2 }}>
                {localizeExplanation(rec.explanation ?? { message: rec.reason })}
              </Txt>
            </View>
          </View>
        )}
      </View>

      {/* actions */}
      <View style={styles.actions}>
        <ReadArticleButton article={article} openedFrom="recommendations" onOpen={onOpen} label={pres.ctaKey ? t(pres.ctaKey) : undefined} style={{ marginRight: 4 }} />
        <SaveButton article={article} />
        <ActionButton label={t("rec.why")} active={why} activeColor={palette.primary} icon="help-circle" onPress={() => setWhy((v) => !v)} />
        <ActionButton
          label={t("rec.readLater")}
          active={readLater}
          activeColor={palette.primary}
          icon="clock"
          onPress={() => {
            setReadLater((v) => !v);
            act("read-later");
          }}
        />
        <View style={[styles.inline, { marginLeft: "auto", gap: 4 }]}>
          <ActionButton
            label={t("rec.like")}
            active={vote === "up"}
            activeColor={palette.positive}
            icon="thumbs-up"
            onPress={() => {
              setVote((v) => (v === "up" ? null : "up"));
              act("like");
            }}
          />
          <ActionButton
            label={t("rec.dislike")}
            active={vote === "down"}
            activeColor={palette.negative}
            icon="thumbs-down"
            onPress={() => {
              act("dislike");
              onDismiss?.();
            }}
          />
          <ActionButton label={t("rec.fb.more")} icon="more-horizontal" onPress={() => setMenu(true)} />
        </View>
      </View>

      <BottomSheet open={menu} onClose={() => setMenu(false)}>
        {VOCAB_MENU.map(({ action, icon, labelKey }) => (
          <SheetItem
            key={action}
            icon={icon}
            label={t(labelKey, { publisher: article.publisher, topic: article.topic || t("rec.fb.thisTopic") })}
            onPress={() => {
              setMenu(false);
              act(action);
              if (action !== "more-topic") onDismiss?.(); // positive signals keep the card
            }}
          />
        ))}
      </BottomSheet>

      <WhyDrawer rec={rec} open={why} />
    </Card>
  );
}

function ActionButton({
  label,
  icon,
  active,
  activeColor,
  onPress,
}: {
  label: string;
  icon: IconName;
  active?: boolean;
  activeColor?: string;
  onPress: () => void;
}) {
  const { palette } = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ selected: !!active }}
      hitSlop={4}
      onPress={onPress}
      style={({ pressed }) => [styles.action, pressed && { backgroundColor: palette.muted }]}
    >
      <Icon name={icon} size={17} color={active ? (activeColor ?? palette.primary) : palette.mutedForeground} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { padding: 20 },
  top: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 },
  inline: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  dismiss: { width: 28, height: 28, borderRadius: radius.sm, alignItems: "center", justifyContent: "center" },
  meta: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 },
  evidence: { marginTop: 16, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.lg, padding: 12 },
  evidenceRow: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  receipt: { marginTop: 10, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, overflow: "hidden" },
  receiptRow: { flexDirection: "row", alignItems: "baseline", gap: 8, paddingHorizontal: 10, paddingVertical: 6 },
  receiptFooter: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, marginTop: 8 },
  actions: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 4, rowGap: 6, marginTop: 16 },
  action: { width: 32, height: 32, borderRadius: radius.lg, alignItems: "center", justifyContent: "center" },
});
