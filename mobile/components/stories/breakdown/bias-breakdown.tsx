import * as React from "react";
import { StyleSheet, View } from "react-native";

import type { StoryCoverage, ViewpointDistribution } from "@ih/core/domain/types";
import { BIAS_BUCKETS, groupOutletsByLean } from "@ih/core/logic/bias-distribution";

import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { BiasDistribution } from "@/components/stories/bias-distribution";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { EmptyBreakdown } from "./empty-breakdown";

/**
 * The BIAS tab: the headline share and L/C/R spectrum counted in OUTLETS, each side's outlets as
 * logo marks, the untracked strip, an explicit callout for every side with ZERO outlets, and the
 * reporting-vs-opinion split when the rows carry registers. Members only (M4).
 */
export function BiasBreakdown({ distribution, coverage }: { distribution: ViewpointDistribution; coverage: StoryCoverage[] }) {
  const { t, formatCompact } = useTranslation();
  const { palette } = useTheme();
  const groups = React.useMemo(() => groupOutletsByLean(coverage), [coverage]);
  const total = distribution.left + distribution.center + distribution.right;

  const missing =
    groups.ratedCount > 0
      ? BIAS_BUCKETS.filter((b) => groups.buckets[b].length === 0)
      : BIAS_BUCKETS.filter((b) => (distribution[b] ?? 0) === 0);

  let reporting = 0;
  let opinion = 0;
  for (const row of coverage) {
    if (row.register === "reporting") reporting += 1;
    else if (row.register === "opinion" || row.register === "mixed") opinion += 1;
  }

  if (total <= 0 && coverage.length === 0) return <EmptyBreakdown>{t("story.bias.none")}</EmptyBreakdown>;

  return (
    <View>
      {groups.ratedCount === 0 && total > 0 && <SpectrumBar distribution={distribution} height={10} />}
      <BiasDistribution groups={groups} />

      {missing.length > 0 && (groups.ratedCount > 0 || total > 0) && (
        <View style={{ marginTop: 12, gap: 6 }}>
          {missing.map((bucket) => (
            <View key={bucket} style={styles.callout}>
              <Icon name="eye-off" size={14} color={palette[bucket]} />
              <Txt size={12} weight="500" color={palette[bucket]}>
                {t("story.noCoverage", { side: t(`filter.${bucket}`).toLowerCase() })}
              </Txt>
            </View>
          ))}
        </View>
      )}

      {reporting + opinion > 0 && (
        <Txt size={12} muted style={[styles.split, { borderTopColor: palette.border }]}>
          {t("story.registerSplit", { reporting: formatCompact(reporting), opinion: formatCompact(opinion) })}
        </Txt>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  callout: { flexDirection: "row", alignItems: "center", gap: 6 },
  split: { marginTop: 12, borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 12 },
});
