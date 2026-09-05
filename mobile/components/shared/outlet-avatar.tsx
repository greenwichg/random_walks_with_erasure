import * as React from "react";
import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import type { OutletMark } from "@ih/core/logic/bias-distribution";
import { hostIconCandidates, logoCandidates } from "@ih/core/logic/publisher-logo";
import { monogram } from "@ih/core/logic/placeholder-art";

import { Txt } from "@/components/ui/text";
import { tw } from "@/design/tailwind";
import { radius } from "@/design/tokens";

import { PublisherLogo } from "./publisher-logo";

/**
 * An outlet's mark on a plate — THE way a third-party logo is presented in the story breakdown.
 *
 * THE PLATE IS ALWAYS WHITE, in both themes: favicons and touch icons are drawn for a light ground,
 * and on the house's `--muted` black wordmarks vanished in dark mode. The logo box is ~2/3 of the
 * plate; the rest is the optical padding that separates a designed chip from a cropped one.
 */
export function OutletAvatar({
  outlet,
  size,
  style,
}: {
  outlet: Pick<OutletMark, "publisher" | "url" | "logo" | "logoFallbacks">;
  size: number;
  style?: StyleProp<ViewStyle>;
}) {
  const icons = logoCandidates(outlet.logo, outlet.logoFallbacks ?? hostIconCandidates(outlet.url));
  const logoPx = Math.round(size * 0.66);
  return (
    <View style={[styles.plate, { width: size, height: size }, style]}>
      <PublisherLogo
        logo={icons[0]}
        fallbacks={icons.slice(1)}
        sizePx={logoPx}
        glyphColor="rgba(0,0,0,0.4)"
        fallbackNode={
          <Txt weight="700" size={Math.round(size * 0.34)} lineHeight={Math.round(size * 0.4)} color="rgba(0,0,0,0.55)">
            {monogram(outlet.publisher)}
          </Txt>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  plate: {
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
    borderRadius: radius.pill,
    backgroundColor: tw.white,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: "rgba(0,0,0,0.1)",
  },
});
