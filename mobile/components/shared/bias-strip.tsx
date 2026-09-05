import * as React from "react";
import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import type { LeanBucket, ViewpointDistribution } from "@ih/core/domain/types";

import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

const SIDES: readonly LeanBucket[] = ["left", "center", "right"];

/** Shares as whole percents that sum to 100, plus the side carrying the most coverage. */
export function biasShares(d: ViewpointDistribution | undefined) {
  const total = (d?.left ?? 0) + (d?.center ?? 0) + (d?.right ?? 0);
  if (total <= 0) return null;
  const pct = (s: LeanBucket) => Math.round(((d?.[s] ?? 0) / total) * 100);
  const shares = { left: pct("left"), center: pct("center"), right: pct("right") };
  const top = SIDES.reduce((a, b) => (shares[b] > shares[a] ? b : a));
  return { shares, top };
}

/**
 * The reference layout's one data mark on every story: a thin three-segment bar (left / centre /
 * right, hairline gaps, square ends) and, in list rows, one caption under it naming the dominant
 * side and the source count. The lead and the topic cards use `labels` instead, printing the share
 * inside each segment. Same distribution the SpectrumBar and coverage plate draw from.
 */
export function BiasStrip({
  distribution,
  sources,
  labels = false,
  style,
}: {
  distribution: ViewpointDistribution | undefined;
  sources?: number;
  labels?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const { t, formatCompact } = useTranslation();
  const { palette } = useTheme();
  const shares = biasShares(distribution);
  if (!shares) return null;
  const { shares: s, top } = shares;

  return (
    <View style={[styles.wrap, style]}>
      <View
        accessible={labels}
        accessibilityLabel={labels ? SIDES.map((side) => `${t(`filter.${side}`)} ${s[side]}%`).join(" · ") : undefined}
        style={[styles.bar, { height: labels ? 18 : 5 }]}
      >
        {SIDES.map((side) => {
          const pct = s[side];
          if (pct <= 0) return null;
          return (
            <View key={side} style={[styles.segment, { flexGrow: pct, backgroundColor: palette[side] }]}>
              {labels && pct >= 12 && (
                <Txt size={10} weight="600" tabular color={palette.card} numberOfLines={1} lineHeight={12} style={styles.label}>
                  {pct >= 22 ? t(`filter.${side}`) : t(`filter.${side}`).charAt(0)} {pct}%
                </Txt>
              )}
            </View>
          );
        })}
      </View>
      {!labels && sources != null && (
        <Txt size={11} muted lineHeight={13} style={{ marginTop: 4 }}>
          {t("storyCard.coverageCaption", { pct: s[top], side: t(`filter.${top}`), n: formatCompact(sources) })}
        </Txt>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { minWidth: 0 },
  bar: { flexDirection: "row", width: "100%", gap: 1, overflow: "hidden", borderRadius: radius.xs },
  segment: { flexBasis: 0, alignItems: "center", justifyContent: "center", overflow: "hidden" },
  label: { paddingHorizontal: 4 },
});
