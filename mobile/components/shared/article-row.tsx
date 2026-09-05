import * as React from "react";
import { StyleSheet, View } from "react-native";

import type { Article } from "@ih/core/domain/types";

import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { EmotionBadge, LeanBadge, RegisterBadge } from "./article-badges";
import { ReadArticleButton } from "./read-article-button";

/** A compact article line item — Local Pulse and the publisher profile's recent list. */
export function ArticleRow({ article, meta, source }: { article: Article; meta?: string; source?: string }) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const sourceLabel =
    source === "recommendations"
      ? t("history.source.recommendations")
      : source === "discover"
        ? t("history.source.discover")
        : source === "stories"
          ? t("history.source.stories")
          : source === "search"
            ? t("history.source.search")
            : source === "saved"
              ? t("history.source.saved")
              : source === "ai-coach"
                ? t("history.source.aiCoach")
                : null;
  return (
    <Card shadow={false} style={styles.card}>
      <View style={styles.row}>
        <View style={{ flex: 1, minWidth: 0 }}>
          <View style={styles.meta}>
            <Txt size={12} weight="500" muted>
              {article.publisher}
            </Txt>
            {article.topic ? (
              <Txt size={12} muted>{`· ${article.topic}`}</Txt>
            ) : null}
            {meta ? <Txt size={12} muted>{`· ${meta}`}</Txt> : null}
          </View>
          <Txt weight="500" size={15} lineHeight={20} style={{ marginTop: 4 }}>
            {article.headline}
          </Txt>
          {sourceLabel && (
            <Txt size={11} muted style={{ marginTop: 4 }}>
              {t("history.openedFrom", { source: sourceLabel })}
            </Txt>
          )}
          <View style={styles.badges}>
            <LeanBadge lean={article.lean} bucket={article.leanBucket} />
            <View style={[styles.badges, { opacity: 0.7, marginTop: 0 }]}>
              {article.register && <RegisterBadge register={article.register} />}
              {article.emotion && <EmotionBadge emotion={article.emotion} dominant={article.dominantEmotion ?? undefined} />}
            </View>
          </View>
        </View>
        <View style={styles.side}>
          <View style={styles.minutes}>
            <Icon name="clock" size={14} color={palette.mutedForeground} />
            <Txt size={12} muted>
              {t("read.estMinutes", { n: article.readingMinutes })}
            </Txt>
          </View>
          <ReadArticleButton article={article} openedFrom="history" />
        </View>
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  card: { padding: 16 },
  row: { flexDirection: "row", alignItems: "flex-start", gap: 16 },
  meta: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6 },
  badges: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6, marginTop: 8 },
  side: { alignItems: "flex-end", gap: 8, flexShrink: 0 },
  minutes: { flexDirection: "row", alignItems: "center", gap: 4 },
});
