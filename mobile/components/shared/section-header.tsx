import * as React from "react";
import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";

/**
 * The editorial section rule — one heading treatment shared by every module: an optional
 * uppercase kicker, the title, a hairline underline, and a trailing "View all →" that is either a
 * link (`href`) or an in-place action (`onAction`).
 */
export function SectionHeader({
  title,
  eyebrow,
  href,
  onAction,
  actionLabel,
  style,
}: {
  title: string;
  eyebrow?: string;
  href?: string;
  onAction?: () => void;
  actionLabel?: string;
  style?: StyleProp<ViewStyle>;
}) {
  const { palette } = useTheme();
  const act = href ? () => navigate(href) : onAction;
  return (
    <View style={[styles.row, { borderBottomColor: palette.border }, style]}>
      <View style={{ flex: 1, minWidth: 0 }}>
        {eyebrow && (
          <Txt size={11} weight="600" uppercase tracking={0.6} muted style={{ marginBottom: 2 }}>
            {eyebrow}
          </Txt>
        )}
        <Txt display weight="600" size={18} lineHeight={24} tight numberOfLines={1} accessibilityRole="header">
          {title}
        </Txt>
      </View>
      {actionLabel && act && (
        <Pressable accessibilityRole={href ? "link" : "button"} onPress={act} hitSlop={6} style={styles.action}>
          <Txt size={12} weight="500" muted>
            {actionLabel}
          </Txt>
          <Icon name="arrow-right" size={14} color={palette.mutedForeground} />
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-between",
    gap: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    paddingBottom: 10,
    marginBottom: 16,
  },
  action: { flexDirection: "row", alignItems: "center", gap: 4, flexShrink: 0 },
});
