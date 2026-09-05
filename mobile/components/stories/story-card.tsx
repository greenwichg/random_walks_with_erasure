import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { Story } from "@ih/core/domain/types";

import { CardImage } from "@/components/shared/card-image";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { track, urlHost } from "@/lib/analytics";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { FreshnessBadge } from "./freshness-badge";

/** A clustered-story preview card — one event, coverage across the spectrum. */
export function StoryCard({ story }: { story: Story }) {
  const { t, formatCompact, timeAgo } = useTranslation();
  const { palette } = useTheme();
  return (
    <Pressable accessibilityRole="link" onPress={() => navigate(`/stories/${story.id}`)} style={({ pressed }) => pressed && { opacity: 0.9 }}>
      <Card style={styles.card}>
        <CardImage
          src={story.image}
          accessibilityLabel={story.title}
          style={{ marginBottom: 12 }}
          onFallback={() => track("story_hero_error", { host: urlHost(story.image), surface: "card" })}
        />
        <View style={styles.top}>
          <View style={styles.chips}>
            {story.topic ? (
              <View style={[styles.topic, { backgroundColor: palette.accent }]}>
                <Txt size={12} weight="500" color={palette.accentForeground} lineHeight={16}>
                  {story.topic}
                </Txt>
              </View>
            ) : null}
            {story.freshness && <FreshnessBadge band={story.freshness.band} score={story.freshness.score} />}
          </View>
          <View style={styles.sources}>
            <Icon name="newspaper" size={14} color={palette.mutedForeground} />
            <Txt size={12} muted>
              {t("storyCard.sources", { n: formatCompact(story.totalCoverage) })}
            </Txt>
          </View>
        </View>
        <Txt display weight="600" size={16} lineHeight={21} tight numberOfLines={2}>
          {story.title}
        </Txt>
        <Txt size={14} muted numberOfLines={2} style={{ marginTop: 6 }}>
          {story.summary}
        </Txt>
        <View style={{ marginTop: 16 }}>
          <SpectrumBar distribution={story.distribution} height={8} showLegend={false} />
        </View>
        <View style={styles.footer}>
          {story.blindspotSide ? (
            <View style={styles.thin}>
              <Icon name="eye-off" size={14} color={palette[story.blindspotSide]} />
              <Txt size={12} weight="500" color={palette[story.blindspotSide]}>
                {t("storyCard.thinOn", { side: t(`filter.${story.blindspotSide}`).toLowerCase() })}
              </Txt>
            </View>
          ) : (
            <Txt size={12} muted>
              {t("storyCard.updated", { time: timeAgo(story.updatedAt) })}
            </Txt>
          )}
          <View style={styles.compare}>
            <Txt size={12} weight="500" style={{ opacity: 0.7 }}>
              {t("storyCard.compare")}
            </Txt>
            <Icon name="arrow-right" size={14} color={palette.foreground} style={{ opacity: 0.7 }} />
          </View>
        </View>
      </Card>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { padding: 20 },
  top: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 8 },
  chips: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 },
  topic: { borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 2 },
  sources: { flexDirection: "row", alignItems: "center", gap: 4, flexShrink: 0 },
  footer: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, marginTop: 12 },
  thin: { flexDirection: "row", alignItems: "center", gap: 4 },
  compare: { flexDirection: "row", alignItems: "center", gap: 2 },
});
