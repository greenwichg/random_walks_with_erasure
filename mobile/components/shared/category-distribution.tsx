import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";
import Svg, { Circle, Path, Text as SvgText } from "react-native-svg";

import { Txt } from "@/components/ui/text";
import { fontFamily } from "@/design/fonts";
import { radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * THE distribution chart: a segmented share bar, a two-ring radial (categories inside, one tick per
 * outlet outside, a readout disc in the hole) and a legend with counts and percentages — all three
 * driven by ONE slice list and ONE selected key, so tapping a legend row or an arc highlights the
 * same category everywhere and swaps the disc's readout to it. Hover became tap; nothing else moved.
 */
export interface DistributionSlice {
  key: string;
  label: string;
  color: string;
  outlets: { publisher: string }[];
  muted?: boolean;
}

const CX = 120;
const CY = 120;
const R_CAT = 76;
const W_CAT = 26;
const R_OUT = 101;
const W_OUT = 12;
const R_HOLE = 56;
const GAP_CAT = 2.5;
const GAP_OUT = 2.2;

const pt = (r: number, deg: number): [number, number] => {
  const a = ((deg - 90) * Math.PI) / 180;
  return [CX + r * Math.cos(a), CY + r * Math.sin(a)];
};
const arcPath = (r: number, a0: number, a1: number) => {
  const [x0, y0] = pt(r, a0);
  const [x1, y1] = pt(r, a1);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
};

export function CategoryDistribution({ slices, defaultKey }: { slices: DistributionSlice[]; defaultKey: string }) {
  const { formatCompact } = useTranslation();
  const { palette } = useTheme();
  const [hover, setHover] = React.useState<string | null>(null);

  const total = slices.reduce((n, s) => n + s.outlets.length, 0);
  if (total === 0) return null;

  const pctOf = (s: DistributionSlice) => Math.round((s.outlets.length / total) * 100);
  const dim = (key: string) => hover !== null && hover !== key;
  const readout = slices.find((s) => s.key === hover) ?? slices.find((s) => s.key === defaultKey) ?? slices[0];
  if (!readout) return null;
  const readoutWords = readout.label.split(" ");

  const slot = 360 / total;
  let angle = 0;
  const catArcs: { s: DistributionSlice; a0: number; a1: number }[] = [];
  const ticks: { publisher: string; key: string; color: string; a0: number; a1: number }[] = [];
  for (const s of slices) {
    const span = s.outlets.length * slot;
    catArcs.push({ s, a0: angle, a1: angle + span });
    s.outlets.forEach((o, i) => {
      const t0 = angle + i * slot;
      ticks.push({ publisher: o.publisher, key: s.key, color: s.color, a0: t0, a1: t0 + slot });
    });
    angle += span;
  }
  const toggle = (key: string) => setHover((h) => (h === key ? null : key));

  return (
    <View>
      <View style={[styles.bar, { backgroundColor: palette.muted }]}>
        {slices.map((s) => (
          <Pressable
            key={s.key}
            onPress={() => toggle(s.key)}
            style={{ width: `${(s.outlets.length / total) * 100}%`, backgroundColor: s.color, opacity: dim(s.key) ? 0.35 : 1 }}
          />
        ))}
      </View>

      <View style={styles.radial}>
        <Svg viewBox="0 0 240 240" width="100%" height="100%" accessibilityLabel={slices.map((s) => `${s.label} · ${s.outlets.length} · ${pctOf(s)}%`).join(", ")}>
          {catArcs.map(({ s, a0, a1 }) =>
            a1 - a0 >= 359.9 ? (
              <Circle key={s.key} cx={CX} cy={CY} r={R_CAT} fill="none" stroke={s.color} strokeWidth={W_CAT} onPress={() => toggle(s.key)} />
            ) : (
              <Path
                key={s.key}
                d={arcPath(R_CAT, a0 + GAP_CAT / 2, a1 - GAP_CAT / 2)}
                fill="none"
                stroke={s.color}
                strokeWidth={W_CAT}
                opacity={dim(s.key) ? 0.35 : 1}
                onPress={() => toggle(s.key)}
              />
            ),
          )}
          {total > 1 &&
            ticks.map(({ publisher, key, color, a0, a1 }) => (
              <Path
                key={publisher}
                d={arcPath(R_OUT, a0 + GAP_OUT / 2, a1 - GAP_OUT / 2)}
                fill="none"
                stroke={color}
                strokeWidth={W_OUT}
                opacity={dim(key) ? 0.35 : 1}
                onPress={() => toggle(key)}
              />
            ))}
          <Circle cx={CX} cy={CY} r={R_HOLE} fill={palette.muted} />
          <SvgText x={CX} y={CY - 6} textAnchor="middle" fontSize={22} fontFamily={fontFamily("700")} fill={palette.foreground}>
            {`${pctOf(readout)}%`}
          </SvgText>
          {readoutWords.slice(0, 2).map((w, i) => (
            <SvgText key={i} x={CX} y={CY + 10 + i * 11} textAnchor="middle" fontSize={9} fontFamily={fontFamily("500")} fill={palette.mutedForeground}>
              {w}
            </SvgText>
          ))}
        </Svg>
      </View>

      <View style={{ marginTop: 12, gap: 2 }}>
        {slices.map((s) => (
          <Pressable
            key={s.key}
            accessibilityRole="button"
            accessibilityState={{ selected: hover === s.key }}
            onPress={() => toggle(s.key)}
            style={({ pressed }) => [styles.legendRow, pressed && { backgroundColor: palette.muted }, dim(s.key) && { opacity: 0.4 }]}
          >
            <View style={[styles.dot, { backgroundColor: s.color }]} />
            <Txt size={12} muted={s.muted} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
              {s.label}
            </Txt>
            <Txt size={12} muted>
              {formatCompact(s.outlets.length)}
            </Txt>
            <Txt size={12} weight="500" tabular align="right" style={{ width: 36 }}>
              {pctOf(s)}%
            </Txt>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: { flexDirection: "row", height: 10, borderRadius: radius.pill, overflow: "hidden" },
  radial: { alignSelf: "center", width: "100%", maxWidth: 240, aspectRatio: 1, marginTop: 12 },
  legendRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 6, paddingVertical: 4, borderRadius: radius.xs },
  dot: { width: 10, height: 10, borderRadius: radius.pill },
});
