import * as React from "react";
import Svg, { Defs, FeGaussianBlur, Filter, G, LinearGradient, Rect, Stop } from "react-native-svg";

import { PLACEHOLDER_HUES } from "@ih/core/logic/placeholder-art";

import { alpha } from "@/design/tokens";
import { hsl } from "@/lib/color";
import { useTheme } from "@/lib/theme";

/**
 * THE default card art — a stack of folded newspapers, drawn in house, for every story or article
 * card whose own image is missing, suspect or dead. The same SVG as `web/components/shared/story-
 * fallback-art.tsx`, coordinate for coordinate, through `react-native-svg`; the theme tokens the
 * web reads through CSS variables are read from the palette here, so the dark theme is still not a
 * bright rectangle punched into a charcoal grid.
 *
 * Decorative, always: it says "no picture was published with this story", and every card names
 * its publisher, topic and coverage in text beside it.
 */

/** Amber, cyan, violet from the curated wheel — the newsprint colour chips. Never 214/356. */
const [AMBER, CYAN, VIOLET] = [PLACEHOLDER_HUES[1], PLACEHOLDER_HUES[4], PLACEHOLDER_HUES[6]];

const SHEETS = [
  { y: 96, skew: -13, ink: 0.05, face: 0.55 },
  { y: 132, skew: -13, ink: 0.07, face: 0.7 },
  { y: 168, skew: -13, ink: 0.06, face: 0.85 },
  { y: 204, skew: -13, ink: 0.05, face: 1 },
] as const;

let counter = 0;

export function StoryFallbackArt() {
  const { palette } = useTheme();
  // Gradient and filter ids must be unique per instance: a list of thumbnails renders many.
  const id = React.useMemo(() => `hvfb${(counter += 1)}`, []);
  const fg = palette.foreground;

  return (
    <Svg viewBox="0 0 400 300" preserveAspectRatio="xMidYMid slice" width="100%" height="100%">
      <Defs>
        <LinearGradient id={`${id}-ground`} x1="0" y1="0" x2="0.3" y2="1">
          <Stop offset="0" stopColor={palette.muted} />
          <Stop offset="1" stopColor={palette.accent} />
        </LinearGradient>
        <Filter id={`${id}-blur`} x="-20%" y="-20%" width="140%" height="140%">
          <FeGaussianBlur stdDeviation="9" />
        </Filter>
      </Defs>

      <Rect width="400" height="300" fill={`url(#${id}-ground)`} />

      {/* Far, defocused newsprint — colour blocks reading as a page that is out of the plane. */}
      <G filter={`url(#${id}-blur)`} opacity={0.5}>
        <Rect x="-10" y="-6" width="420" height="104" fill={palette.card} />
        <Rect x="16" y="10" width="128" height="58" rx="6" fill={alpha(hsl(CYAN, 62, 52), 0.5)} />
        <Rect x="166" y="4" width="96" height="40" rx="6" fill={alpha(hsl(AMBER, 78, 55), 0.45)} />
        <Rect x="150" y="52" width="220" height="34" rx="6" fill={alpha(hsl(VIOLET, 50, 58), 0.4)} />
      </G>

      {/* The stack: folded sheets running lower-left to upper-right, each lifted off the last. */}
      {SHEETS.map((sheet, i) => (
        <G key={i} transform={`rotate(${sheet.skew} 200 ${sheet.y}) translate(0 ${sheet.y})`}>
          <Rect x="-60" y="-9" width="520" height="12" fill={alpha(fg, sheet.ink)} />
          <Rect x="-60" y="0" width="520" height="34" fill={palette.card} />
          <Rect x="-60" y="32" width="520" height="2" fill={alpha(fg, 0.08)} />
          <G opacity={sheet.face} fill={alpha(fg, 0.72)}>
            <Rect x="24" y="8" width={54 + i * 22} height="15" rx="2" />
            <Rect x={86 + i * 22} y="8" width={34 + i * 8} height="15" rx="2" />
            {i > 1 && <Rect x={128 + i * 30} y="8" width="46" height="15" rx="2" />}
          </G>
          <G opacity={sheet.face * 0.5} fill={alpha(fg, 0.28)}>
            {[0, 1, 2, 3, 4].map((c) => (
              <Rect key={c} x={236 + c * 30} y="10" width="18" height="11" rx="1" />
            ))}
          </G>
        </G>
      ))}

      {/* The colour chips down the right edge — a sports or listings section in the stack. */}
      <G transform="rotate(-13 200 150)">
        <Rect x="330" y="118" width="22" height="16" rx="3" fill={hsl(CYAN, 60, 48)} opacity={0.85} />
        <Rect x="356" y="118" width="22" height="16" rx="3" fill={hsl(AMBER, 80, 55)} opacity={0.85} />
        <Rect x="330" y="154" width="22" height="16" rx="3" fill={hsl(VIOLET, 48, 55)} opacity={0.8} />
      </G>

      <Rect width="400" height="300" fill={alpha(fg, 0.03)} />
    </Svg>
  );
}
