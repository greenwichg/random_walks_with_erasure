import * as React from "react";
import { StyleSheet, Text, type StyleProp, type TextProps, type TextStyle } from "react-native";

import { fontFamily, type Weight } from "@/design/fonts";
import { useTheme } from "@/lib/theme";

/**
 * The one text primitive.
 *
 * Every string in the app goes through this so three things happen in one place: the typeface is
 * chosen by weight (custom faces on native must be selected by FAMILY, never by `fontWeight` —
 * see design/fonts.ts), the colour defaults to the theme's foreground rather than the platform's
 * black, and the web's `tracking-tight` / `tabular-nums` idioms have a spelling here.
 *
 * `size` is the font size in px; `lineHeight` follows the web's `leading-*` classes when given and
 * is otherwise a comfortable 1.4. `display` selects Schibsted Grotesk — the web's h1–h3 face.
 */
export function Txt({
  size = 15,
  weight = "400",
  display = false,
  color,
  muted = false,
  lineHeight,
  tight = false,
  tabular = false,
  uppercase = false,
  tracking,
  align,
  style,
  children,
  ...rest
}: TextProps & {
  size?: number;
  weight?: Weight;
  display?: boolean;
  color?: string;
  /** `text-muted-foreground`. */
  muted?: boolean;
  lineHeight?: number;
  /** `tracking-tight` (-0.025em). */
  tight?: boolean;
  tabular?: boolean;
  uppercase?: boolean;
  /** Letter spacing in px; `tracking-wider` ≈ 0.05em. */
  tracking?: number;
  align?: TextStyle["textAlign"];
}) {
  const { palette } = useTheme();
  const computed: TextStyle = {
    fontFamily: fontFamily(weight, display ? "display" : "sans"),
    fontSize: size,
    lineHeight: lineHeight ?? Math.round(size * 1.4),
    color: color ?? (muted ? palette.mutedForeground : palette.foreground),
    ...(tight ? { letterSpacing: -0.025 * size } : {}),
    ...(tracking != null ? { letterSpacing: tracking } : {}),
    ...(tabular ? { fontVariant: ["tabular-nums"] } : {}),
    ...(uppercase ? { textTransform: "uppercase" } : {}),
    ...(align ? { textAlign: align } : {}),
  };
  return (
    <Text {...rest} style={[computed, style as StyleProp<TextStyle>]}>
      {children}
    </Text>
  );
}

/** A `Txt` that never wraps: `truncate`. */
export function Truncate(props: React.ComponentProps<typeof Txt>) {
  return <Txt numberOfLines={1} ellipsizeMode="tail" {...props} />;
}

export const textStyles = StyleSheet.create({
  /** The web's `text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground` kicker. */
  kicker: { letterSpacing: 0.6 },
});
