import * as React from "react";
import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import type { ViewpointDistribution } from "@ih/core/domain/types";

import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

/**
 * A left / center / right segmented bar with legend — the political-distribution primitive,
 * reused on the story hero and the bias breakdown. The web's width animation is dropped (the bar
 * is decorative; the legend carries the numbers).
 */
export function SpectrumBar({
  distribution,
  height = 12,
  showLegend = true,
  style,
}: {
  distribution: ViewpointDistribution;
  height?: number;
  showLegend?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const { palette } = useTheme();
  const segments = [
    { key: "left", label: "Left", value: distribution.left, color: palette.left },
    { key: "center", label: "Center", value: distribution.center, color: palette.center },
    { key: "right", label: "Right", value: distribution.right, color: palette.right },
  ];
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;

  return (
    <View style={[styles.wrap, style]}>
      <View style={[styles.bar, { height, backgroundColor: palette.muted }]}>
        {segments.map((s) => (
          <View key={s.key} style={{ width: `${(s.value / total) * 100}%`, backgroundColor: s.color }} />
        ))}
      </View>
      {showLegend && (
        <View style={styles.legend}>
          {segments.map((s) => (
            <View key={s.key} style={styles.item}>
              <View style={[styles.dot, { backgroundColor: s.color }]} />
              <Txt size={12} muted>
                {s.label}
              </Txt>
              <Txt size={12} weight="500" tabular>
                {Math.round((s.value / total) * 100)}%
              </Txt>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 10 },
  bar: { flexDirection: "row", overflow: "hidden", borderRadius: radius.pill },
  legend: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  item: { flexDirection: "row", alignItems: "center", gap: 6 },
  dot: { width: 8, height: 8, borderRadius: radius.pill },
});
