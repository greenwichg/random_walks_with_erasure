import * as React from "react";
import { Pressable, StyleSheet, type StyleProp, type ViewStyle } from "react-native";

import { alpha, radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

import { Txt } from "./text";

/**
 * `ui/filter-chip.tsx`: the one filter/toggle chip — a pill with an active state and an optional
 * count. `accessibilityState.selected` carries what `aria-pressed` carries on the web.
 */
export function FilterChip({
  label,
  count,
  active,
  onPress,
  style,
}: {
  label: React.ReactNode;
  count?: number;
  active: boolean;
  onPress: () => void;
  style?: StyleProp<ViewStyle>;
}) {
  const { palette } = useTheme();
  const fg = active ? palette.primary : palette.mutedForeground;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.chip,
        {
          borderColor: active ? palette.primary : palette.border,
          backgroundColor: active ? alpha(palette.primary, 0.1) : pressed ? palette.accent : palette.card,
        },
        style,
      ]}
    >
      {typeof label === "string" ? (
        <Txt size={12} weight="500" color={fg} lineHeight={16}>
          {label}
        </Txt>
      ) : (
        label
      )}
      {count != null && (
        <Txt size={12} weight="500" color={fg} tabular lineHeight={16} style={{ opacity: 0.6 }}>
          {count}
        </Txt>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: radius.pill,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
});
