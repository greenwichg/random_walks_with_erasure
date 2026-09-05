import * as React from "react";
import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import { alpha, radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

import { Icon, type IconName } from "./icon";
import { Txt } from "./text";

export type BadgeVariant =
  | "default"
  | "secondary"
  | "outline"
  | "positive"
  | "caution"
  | "negative"
  | "left"
  | "center"
  | "right";

/**
 * `ui/badge.tsx`: a pill (`rounded-full border px-2.5 py-0.5 text-xs font-medium`).
 *
 * The lean pills carry a visible tint AND a coloured hairline (`/15` fill, `/30` border): on the
 * dark card a borderless `/12` fill vanished and "Lean left" read as a bare blue link — a
 * misleading affordance on a political signal. Same rule here, same numbers.
 */
export function Badge({
  variant = "default",
  icon,
  children,
  style,
}: {
  variant?: BadgeVariant;
  icon?: IconName;
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  const { palette } = useTheme();
  const lean = variant === "left" || variant === "center" || variant === "right";
  const colors: { bg: string; fg: string; border: string } = lean
    ? { bg: alpha(palette[variant], 0.15), fg: palette[variant], border: alpha(palette[variant], 0.3) }
    : variant === "secondary"
      ? { bg: palette.secondary, fg: palette.secondaryForeground, border: "transparent" }
      : variant === "outline"
        ? { bg: "transparent", fg: palette.foreground, border: palette.border }
        : variant === "positive"
          ? { bg: alpha(palette.positive, 0.12), fg: palette.positive, border: "transparent" }
          : variant === "caution"
            ? { bg: alpha(palette.caution, 0.15), fg: palette.caution, border: "transparent" }
            : variant === "negative"
              ? { bg: alpha(palette.negative, 0.12), fg: palette.negative, border: "transparent" }
              : { bg: alpha(palette.primary, 0.1), fg: palette.primary, border: "transparent" };

  return (
    <View style={[styles.pill, { backgroundColor: colors.bg, borderColor: colors.border }, style]}>
      {icon && <Icon name={icon} size={12} color={colors.fg} />}
      {typeof children === "string" ? (
        <Txt size={12} weight="500" color={colors.fg} lineHeight={16}>
          {children}
        </Txt>
      ) : (
        children
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    alignSelf: "flex-start",
    borderWidth: 1,
    borderRadius: radius.pill,
    paddingHorizontal: 10,
    paddingVertical: 2,
  },
});
