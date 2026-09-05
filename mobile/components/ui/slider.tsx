import * as React from "react";
import { PanResponder, StyleSheet, View, type LayoutChangeEvent, type StyleProp, type ViewStyle } from "react-native";

import { alpha, radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

/**
 * `ui/slider.tsx` (Radix on the web): a 6px track at `primary/20`, the filled range in `primary`,
 * a 16px thumb on a `background` disc with a primary hairline. One gesture handles a tap anywhere on
 * the track and a drag of the thumb, snapped to `step`. No dependency — the settings screen is the
 * only caller and eleven sliders do not justify a native module.
 */
export function Slider({
  value,
  min = 0,
  max = 100,
  step = 1,
  onChange,
  accessibilityLabel,
  style,
}: {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (v: number) => void;
  accessibilityLabel?: string;
  style?: StyleProp<ViewStyle>;
}) {
  const { palette } = useTheme();
  const width = React.useRef(0);
  const latest = React.useRef(value);
  latest.current = value;

  const valueAt = React.useCallback(
    (x: number) => {
      const w = width.current || 1;
      const raw = min + (Math.max(0, Math.min(w, x)) / w) * (max - min);
      const snapped = Math.round(raw / step) * step;
      return Math.max(min, Math.min(max, snapped));
    },
    [min, max, step],
  );

  const pan = React.useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderGrant: (e) => onChange(valueAt(e.nativeEvent.locationX)),
        onPanResponderMove: (e) => {
          const v = valueAt(e.nativeEvent.locationX);
          if (v !== latest.current) onChange(v);
        },
      }),
    [onChange, valueAt],
  );

  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0;

  return (
    <View
      accessibilityRole="adjustable"
      accessibilityLabel={accessibilityLabel}
      accessibilityValue={{ min, max, now: value }}
      onAccessibilityAction={(e) => {
        if (e.nativeEvent.actionName === "increment") onChange(Math.min(max, value + step));
        if (e.nativeEvent.actionName === "decrement") onChange(Math.max(min, value - step));
      }}
      accessibilityActions={[{ name: "increment" }, { name: "decrement" }]}
      onLayout={(e: LayoutChangeEvent) => {
        width.current = e.nativeEvent.layout.width;
      }}
      style={[styles.hit, style]}
      {...pan.panHandlers}
    >
      <View style={[styles.track, { backgroundColor: alpha(palette.primary, 0.2) }]}>
        <View style={[styles.range, { width: `${pct}%`, backgroundColor: palette.primary }]} />
      </View>
      <View
        pointerEvents="none"
        style={[
          styles.thumb,
          { left: `${pct}%`, backgroundColor: palette.background, borderColor: alpha(palette.primary, 0.5) },
        ]}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  hit: { height: 32, justifyContent: "center" },
  track: { height: 6, borderRadius: radius.pill, overflow: "hidden" },
  range: { height: "100%", borderRadius: radius.pill },
  thumb: {
    position: "absolute",
    top: 8,
    width: 16,
    height: 16,
    marginLeft: -8,
    borderRadius: radius.pill,
    borderWidth: 1,
    shadowColor: "#000",
    shadowOpacity: 0.12,
    shadowRadius: 2,
    shadowOffset: { width: 0, height: 1 },
    elevation: 2,
  },
});
