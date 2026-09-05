import * as React from "react";
import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import { labelledItems } from "@ih/core/logic/bar-items";

import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

import { ChartEmpty } from "./states";

export interface BarItem {
  label: string;
  value: number; // 0–1 share
  count?: number;
  color?: string;
  sublabel?: string;
}

/** A ranked horizontal bar list — used for topic + source distributions. */
export function BarList({ items: given, style }: { items: BarItem[]; style?: StyleProp<ViewStyle> }) {
  const { palette } = useTheme();
  const items = labelledItems(given);
  if (!items.length) return <ChartEmpty style={style} />;
  const max = Math.max(...items.map((i) => i.value), 0.0001);
  return (
    <View style={[styles.list, style]}>
      {items.map((item) => (
        <View key={item.label}>
          <View style={styles.row}>
            <View style={styles.labels}>
              <Txt size={14} weight="500" numberOfLines={1} style={{ flexShrink: 1 }}>
                {item.label}
              </Txt>
              {item.sublabel && (
                <Txt size={12} muted>
                  {item.sublabel}
                </Txt>
              )}
            </View>
            <Txt size={14} muted tabular>
              {Math.round(item.value * 100)}%
              {typeof item.count === "number" ? <Txt size={14} muted style={{ opacity: 0.6 }}>{`  · ${item.count}`}</Txt> : null}
            </Txt>
          </View>
          <View style={[styles.track, { backgroundColor: palette.muted }]}>
            <View
              style={[styles.fill, { width: `${(item.value / max) * 100}%`, backgroundColor: item.color ?? palette.primary }]}
            />
          </View>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  list: { gap: 12 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 4 },
  labels: { flexDirection: "row", alignItems: "center", gap: 8, flex: 1, minWidth: 0 },
  track: { height: 8, borderRadius: radius.pill, overflow: "hidden" },
  fill: { height: "100%", borderRadius: radius.pill },
});
