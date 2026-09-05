import * as React from "react";
import { StyleSheet, View } from "react-native";

import type { StoryCoverage } from "@ih/core/domain/types";
import { dominantOwnership, groupOutletsByOwnership } from "@ih/core/logic/ownership-distribution";

import { CategoryDistribution, type DistributionSlice } from "@/components/shared/category-distribution";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { OWNERSHIP_LABEL_KEY, ownershipColor } from "@/lib/meta";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { EmptyBreakdown } from "./empty-breakdown";

/**
 * The OWNERSHIP tab: who controls the outlets on this story. One summary sentence over the shared
 * distribution chart. Outlets the registry doesn't classify form the muted `unknown` slice, counted
 * in every percentage and never folded into "other". Members only (M4).
 */
export function OwnershipBreakdown({ coverage }: { coverage: StoryCoverage[] }) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const groups = React.useMemo(() => groupOutletsByOwnership(coverage), [coverage]);
  const dominant = dominantOwnership(groups);

  if (groups.knownCount === 0 || !dominant) return <EmptyBreakdown>{t("story.ownership.none")}</EmptyBreakdown>;

  const slices: DistributionSlice[] = groups.slices.map((s) => ({
    key: s.category,
    label: t(OWNERSHIP_LABEL_KEY[s.category]),
    color: ownershipColor(s.category, palette),
    outlets: s.outlets,
    muted: s.category === "unknown",
  }));

  return (
    <View>
      <View style={styles.summary}>
        <View style={[styles.dot, { backgroundColor: ownershipColor(dominant.category, palette) }]} />
        <Txt size={12} weight="500" muted>
          {t("story.ownershipSummary", { pct: dominant.pct, category: t(OWNERSHIP_LABEL_KEY[dominant.category]) })}
        </Txt>
      </View>
      <CategoryDistribution slices={slices} defaultKey={dominant.category} />
    </View>
  );
}

const styles = StyleSheet.create({
  summary: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 10 },
  dot: { width: 6, height: 6, borderRadius: radius.pill },
});
