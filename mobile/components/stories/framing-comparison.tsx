import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { StoryCoverage } from "@ih/core/domain/types";
import { framingComparison } from "@ih/core/logic/framing";

import { ReadArticleButton } from "@/components/shared/read-article-button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * "How each side frames it" — the same event's headline from each rated side, next to each other.
 * Derivation lives in `@ih/core/logic/framing`; this renders what it returns and disappears when it
 * returns null. The side is encoded twice (colour rail + text label).
 */
export function FramingComparison({ coverage }: { coverage: StoryCoverage[] }) {
  const { t, timeAgo } = useTranslation();
  const { palette } = useTheme();
  const sides = framingComparison(coverage);
  if (!sides) return null;

  return (
    <View style={{ gap: 12 }}>
      {sides.map(({ side, row, count }) => {
        const color = palette[side];
        return (
          <Card key={side} shadow={false} style={styles.card}>
            <View style={[styles.rail, { backgroundColor: color }]} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <View style={styles.sideRow}>
                <Txt size={11} weight="600" uppercase tracking={0.5} color={color}>
                  {t(`filter.${side}`)}
                </Txt>
                <Txt size={11} muted>
                  {t("stories.framing.sources", { n: count })}
                </Txt>
              </View>
              <View style={{ marginBottom: 8 }}>
                <Icon name="quote" size={12} color={palette.mutedForeground} style={{ marginBottom: 4 }} />
                <Txt display weight="600" size={14} lineHeight={19} tight>
                  {row.headline}
                </Txt>
              </View>
              <View style={styles.meta}>
                <Pressable accessibilityRole="link" onPress={() => navigate(`/publishers/${encodeURIComponent(row.publisher)}`)} hitSlop={4}>
                  <Txt size={12} weight="500">
                    {row.publisher}
                  </Txt>
                </Pressable>
                <Txt size={12} muted>{`· ${timeAgo(row.publishedAt)}`}</Txt>
                {row.register && row.register !== "reporting" && <Txt size={12} muted>{`· ${t(`register.${row.register}`)}`}</Txt>}
              </View>
              {row.url && (
                <ReadArticleButton article={{ url: row.url, headline: row.headline }} openedFrom="stories" style={{ marginTop: 8 }} />
              )}
            </View>
          </Card>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { flexDirection: "row", gap: 12, padding: 16, borderRadius: radius.md },
  rail: { width: 4, borderRadius: radius.pill },
  sideRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 4 },
  meta: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6 },
});
