import * as React from "react";
import { ActivityIndicator, Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import { alpha, radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

import { Icon, type IconName } from "./icon";
import { Txt } from "./text";

type Variant = "default" | "secondary" | "outline" | "ghost" | "destructive" | "link";
type Size = "default" | "sm" | "lg" | "icon";

/**
 * `ui/button.tsx`, on native: the same six variants and four sizes (h-9 / h-8 / h-11 / 9×9),
 * `touch-target` implied by `hitSlop`, and `active:scale-[0.98]` as the pressed state. A string
 * child is set in the button's own type; a node child is laid out beside the icon unchanged.
 */
export function Button({
  variant = "default",
  size = "default",
  icon,
  iconRight,
  children,
  onPress,
  disabled = false,
  loading = false,
  full = false,
  style,
  accessibilityLabel,
  textColor,
}: {
  variant?: Variant;
  size?: Size;
  icon?: IconName;
  iconRight?: IconName;
  children?: React.ReactNode;
  onPress?: () => void;
  disabled?: boolean;
  loading?: boolean;
  /** `w-full`. */
  full?: boolean;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
  textColor?: string;
}) {
  const { palette } = useTheme();

  const fg =
    textColor ??
    (variant === "default"
      ? palette.primaryForeground
      : variant === "destructive"
        ? palette.destructiveForeground
        : variant === "secondary"
          ? palette.secondaryForeground
          : variant === "link"
            ? palette.primary
            : palette.foreground);

  const surface = (pressed: boolean): ViewStyle => {
    switch (variant) {
      case "default":
        return { backgroundColor: pressed ? alpha(palette.primary, 0.9) : palette.primary };
      case "secondary":
        return { backgroundColor: pressed ? alpha(palette.secondary, 0.8) : palette.secondary };
      case "outline":
        return {
          borderWidth: 1,
          borderColor: palette.input,
          backgroundColor: pressed ? palette.accent : "transparent",
        };
      case "ghost":
        return { backgroundColor: pressed ? palette.accent : "transparent" };
      case "destructive":
        return { backgroundColor: pressed ? alpha(palette.destructive, 0.9) : palette.destructive };
      default:
        return {};
    }
  };

  const height = size === "sm" ? 32 : size === "lg" ? 44 : 36;
  const px = size === "sm" ? 12 : size === "lg" ? 24 : size === "icon" ? 0 : 16;
  const fontSize = size === "sm" ? 12 : 14;
  const iconSize = 16;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{ disabled: disabled || loading }}
      disabled={disabled || loading}
      onPress={onPress}
      hitSlop={size === "sm" || size === "icon" ? 6 : 0}
      style={({ pressed }) => [
        styles.base,
        {
          height,
          paddingHorizontal: px,
          width: size === "icon" ? 36 : undefined,
          borderRadius: size === "lg" ? radius.lg : radius.md,
          opacity: disabled ? 0.5 : 1,
          transform: [{ scale: pressed && !disabled ? 0.98 : 1 }],
        },
        full && styles.full,
        surface(pressed),
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator size="small" color={fg} />
      ) : (
        <View style={styles.row}>
          {icon && <Icon name={icon} size={iconSize} color={fg} />}
          {typeof children === "string" ? (
            <Txt
              size={fontSize}
              weight="500"
              color={fg}
              numberOfLines={1}
              style={variant === "link" ? styles.link : undefined}
            >
              {children}
            </Txt>
          ) : (
            children
          )}
          {iconRight && <Icon name={iconRight} size={iconSize} color={fg} />}
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: { alignItems: "center", justifyContent: "center", flexDirection: "row", alignSelf: "flex-start" },
  full: { alignSelf: "stretch" },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  link: { textDecorationLine: "underline" },
});
