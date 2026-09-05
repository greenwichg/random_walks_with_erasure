import * as React from "react";
import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import { radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

/** `rounded-lg border bg-card shadow-soft` — the surface every card, panel and section sits on. */
export function Card({
  children,
  style,
  padded = true,
  shadow = true,
}: {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  padded?: boolean;
  shadow?: boolean;
}) {
  const { palette } = useTheme();
  return (
    <View
      style={[
        styles.card,
        { backgroundColor: palette.card, borderColor: palette.border },
        shadow && styles.shadow,
        padded && styles.padded,
        style,
      ]}
    >
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.lg, overflow: "hidden" },
  padded: { padding: 16 },
  /** `shadow-soft`. */
  shadow: {
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 3,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
});
