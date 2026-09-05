import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { StoryCoverage } from "@ih/core/domain/types";
import { dominantFactuality, factualityAttribution, groupOutletsByFactuality } from "@ih/core/logic/factuality-distribution";

import { CategoryDistribution, type DistributionSlice } from "@/components/shared/category-distribution";
import { FactualityBadge } from "@/components/shared/factuality-badge";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { FACTUALITY_LABEL_KEY, factualityColor } from "@/lib/meta";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { EmptyBreakdown } from "./empty-breakdown";

const DATE_OPTS: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "numeric" };

/**
 * The FACTUALITY tab: how the outlets on this story are rated for factual reporting — the same
 * chart the Ownership tab draws over the rater's own six-level scale, plus the ATTRIBUTION line
 * and a full breakdown behind it naming every outlet, its level, who rated it and when. Three
 * absences, three different sentences.
 */
export function FactualityBreakdown({ coverage, published }: { coverage: StoryCoverage[]; published?: boolean }) {
  const { t, formatDate } = useTranslation();
  const { palette } = useTheme();
  const [showAll, setShowAll] = React.useState(false);
  const groups = React.useMemo(() => groupOutletsByFactuality(coverage), [coverage]);
  const dominant = dominantFactuality(groups);
  const credit = factualityAttribution(groups);

  if (!published) return <EmptyBreakdown>{t("story.factuality.unpublished")}</EmptyBreakdown>;
  if (groups.ratedCount === 0 || !dominant) return <EmptyBreakdown>{t("story.factuality.none")}</EmptyBreakdown>;

  const slices: DistributionSlice[] = groups.slices.map((s) => ({
    key: s.level,
    label: t(FACTUALITY_LABEL_KEY[s.level]),
    color: factualityColor(s.level, palette),
    outlets: s.outlets,
    muted: s.level === "unrated",
  }));
  const rated = groups.slices.flatMap((s) => s.outlets.filter((o) => o.rating));

  return (
    <View>
      <View style={styles.summary}>
        <View style={[styles.dot, { backgroundColor: factualityColor(dominant.level, palette) }]} />
        <Txt size={12} weight="500" muted>
          {t("story.factualitySummary", { pct: dominant.pct, level: t(FACTUALITY_LABEL_KEY[dominant.level]) })}
        </Txt>
      </View>

      <CategoryDistribution slices={slices} defaultKey={dominant.level} />

      {credit && (
        <Txt size={11} muted lineHeight={16} style={{ marginTop: 12 }}>
          {t("story.factuality.attribution", {
            sources: credit.sources.map((s) => t(`publishers.factuality.source.${s}`)).join(", "),
            date: formatDate(credit.asOf, DATE_OPTS),
          })}
        </Txt>
      )}

      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: showAll }}
        onPress={() => setShowAll((v) => !v)}
        style={({ pressed }) => [styles.seeFull, { borderColor: palette.border, backgroundColor: pressed ? palette.accent : "transparent" }]}
      >
        <Txt size={12} weight="600">
          {t("story.factuality.seeFull")}
        </Txt>
        <Icon name={showAll ? "chevron-up" : "chevron-down"} size={14} />
      </Pressable>

      {showAll && (
        <View style={[styles.list, { borderColor: palette.border }]}>
          {rated.map((outlet, i) => (
            <View key={outlet.publisher} style={[styles.item, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.border }]}>
              <Pressable accessibilityRole="link" onPress={() => navigate(`/publishers/${encodeURIComponent(outlet.publisher)}`)}>
                <Txt size={12} weight="500" numberOfLines={1}>
                  {outlet.publisher}
                </Txt>
              </Pressable>
              <View style={{ marginTop: 4 }}>
                <FactualityBadge factuality={outlet.rating} />
              </View>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  summary: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 10 },
  dot: { width: 6, height: 6, borderRadius: radius.pill },
  seeFull: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, borderWidth: 1, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 8 },
  list: { marginTop: 8, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, overflow: "hidden" },
  item: { paddingHorizontal: 12, paddingVertical: 8 },
});
