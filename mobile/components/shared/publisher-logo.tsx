import * as React from "react";
import { Image, PixelRatio, type StyleProp, type ImageStyle } from "react-native";

import { isTooLowRes, logoCandidates, nextCandidate } from "@ih/core/logic/publisher-logo";

import { Icon } from "@/components/ui/icon";
import { useTheme } from "@/lib/theme";

/**
 * A publisher's mark, at the best resolution the publisher actually exposes.
 *
 * Walks the candidate chain the engine supplies (curated → Wikimedia → Apple touch icon →
 * favicon), dropping to the next one when an image fails to load OR loads too small for the box it
 * has to fill — the same rule, from the same shared module, as the web. When the chain runs out,
 * the glyph, or the caller's `fallbackNode` (a monogram on the chips).
 */
export function PublisherLogo({
  logo,
  fallbacks,
  sizePx,
  style,
  fallbackNode,
  glyphColor,
}: {
  logo?: string | null;
  fallbacks?: string[] | null;
  /** The CSS size of the CONTENT box — what decides how many real pixels the image needs. */
  sizePx: number;
  style?: StyleProp<ImageStyle>;
  fallbackNode?: React.ReactNode;
  glyphColor?: string;
}) {
  const { palette } = useTheme();
  const candidates = React.useMemo(() => logoCandidates(logo, fallbacks), [logo, fallbacks]);
  const [current, setCurrent] = React.useState<string | null>(() => candidates[0] ?? null);

  // A new publisher restarts the walk — a recycled list row must not keep the previous outlet's
  // exhausted state.
  React.useEffect(() => setCurrent(candidates[0] ?? null), [candidates]);

  const advance = React.useCallback(() => setCurrent((c) => nextCandidate(candidates, c)), [candidates]);

  if (!current) {
    if (fallbackNode !== undefined) return <>{fallbackNode}</>;
    return <Icon name="building" size={sizePx} color={glyphColor ?? palette.mutedForeground} />;
  }

  return (
    <Image
      source={{ uri: current }}
      resizeMode="contain"
      accessible={false}
      style={[{ width: sizePx, height: sizePx }, style]}
      onError={advance}
      onLoad={(e) => {
        const width = e.nativeEvent?.source?.width ?? 0;
        if (width > 0 && isTooLowRes(width, sizePx, PixelRatio.get(), current)) advance();
      }}
    />
  );
}
