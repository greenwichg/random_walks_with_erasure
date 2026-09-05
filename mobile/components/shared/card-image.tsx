import * as React from "react";
import { Image, StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import { radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

import { StoryFallbackArt } from "./story-fallback-art";

/**
 * THE card image slot — never empty: a card either shows its own art or the shared newspaper
 * fallback, in the identical box, at the identical aspect, cropped the same way (`object-cover`).
 *
 * Four ways a card ends up without its own art, one outcome: no image; the engine flagged the
 * image as branding (`suspect`); the URL is dead or hotlink-protected, which only this device can
 * discover, so the swap happens on the load error; the URL decodes to nothing.
 */
export function CardImage({
  src,
  aspect = 16 / 9,
  suspect = false,
  onFallback,
  style,
  radiusPx = radius.lg,
  accessibilityLabel,
}: {
  src?: string | null;
  /** Width / height. `1` for a square thumbnail, `21/9` for the story hero. */
  aspect?: number;
  suspect?: boolean;
  /** Fired when a load ERROR (not absence) hands the slot to the fallback. */
  onFallback?: () => void;
  style?: StyleProp<ViewStyle>;
  radiusPx?: number;
  /** Empty for a decorative thumbnail whose card already names the story in text. */
  accessibilityLabel?: string;
}) {
  const { palette } = useTheme();
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [src]);

  const usable = Boolean(src) && !suspect && !failed;

  return (
    <View
      style={[styles.box, { aspectRatio: aspect, borderRadius: radiusPx, backgroundColor: palette.muted }, style]}
      accessible={Boolean(accessibilityLabel)}
      accessibilityLabel={accessibilityLabel}
      accessibilityRole={accessibilityLabel ? "image" : undefined}
    >
      {usable ? (
        <Image
          source={{ uri: src ?? undefined }}
          resizeMode="cover"
          style={styles.fill}
          onError={() => {
            setFailed(true);
            onFallback?.();
          }}
        />
      ) : (
        <View style={styles.fill} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
          <StoryFallbackArt />
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { width: "100%", overflow: "hidden" },
  fill: { width: "100%", height: "100%" },
});
