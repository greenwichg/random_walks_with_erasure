import * as React from "react";
import { Animated, Easing, type StyleProp, type ViewStyle } from "react-native";

import { radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

/** `ui/skeleton.tsx`: a muted block that pulses. Sized by the caller, like its web namesake. */
export function Skeleton({ style, height, width }: { style?: StyleProp<ViewStyle>; height?: number; width?: number | `${number}%` }) {
  const { palette } = useTheme();
  const pulse = React.useRef(new Animated.Value(1)).current;

  React.useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 0.5, duration: 900, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 1, duration: 900, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [pulse]);

  return (
    <Animated.View
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      style={[
        { backgroundColor: palette.muted, borderRadius: radius.md, opacity: pulse },
        height != null && { height },
        width != null && { width },
        style,
      ]}
    />
  );
}
