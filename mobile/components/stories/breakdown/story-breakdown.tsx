import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { Story } from "@ih/core/domain/types";
import { splitCoverage } from "@ih/core/logic/story-attached";

import { InfoTooltip } from "@/components/shared/info-tooltip";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { BiasBreakdown } from "./bias-breakdown";
import { FactualityBreakdown } from "./factuality-breakdown";
import { OwnershipBreakdown } from "./ownership-breakdown";

const TABS = ["bias", "factuality", "ownership"] as const;
type Tab = (typeof TABS)[number];

const TAB_META: Record<Tab, { labelKey: string; infoKey: string }> = {
  bias: { labelKey: "story.bias", infoKey: "story.biasInfo" },
  factuality: { labelKey: "story.factuality", infoKey: "story.factualityInfo" },
  ownership: { labelKey: "story.ownership", infoKey: "story.ownershipInfo" },
};

/**
 * THE story breakdown — one card, three tabs (Bias · Factuality · Ownership). Every tab draws from
 * the story's OWN member coverage rows; nothing is derived from another tab. Inside the collapsible
 * panel the tab's own explanation stays (it is not chrome) — it moves in beside the tablist.
 */
export function StoryBreakdown({ story }: { story: Story }) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const [tab, setTab] = React.useState<Tab>("bias");
  const coverage = React.useMemo(() => splitCoverage(story.coverage).panel, [story.coverage]);

  return (
    <View>
      <View style={styles.infoRow}>
        <InfoTooltip text={t(TAB_META[tab].infoKey)} />
      </View>
      <View accessibilityRole="tablist" accessibilityLabel={t("story.breakdown")} style={[styles.tablist, { backgroundColor: palette.muted }]}>
        {TABS.map((key) => {
          const active = tab === key;
          return (
            <Pressable
              key={key}
              accessibilityRole="tab"
              accessibilityState={{ selected: active }}
              onPress={() => setTab(key)}
              style={[styles.tab, active && { backgroundColor: palette.card, ...styles.activeShadow }]}
            >
              <Txt size={12} weight="600" color={active ? palette.foreground : palette.mutedForeground} numberOfLines={1}>
                {t(TAB_META[key].labelKey)}
              </Txt>
            </Pressable>
          );
        })}
      </View>
      <View>
        {tab === "bias" && <BiasBreakdown distribution={story.distribution} coverage={coverage} />}
        {tab === "factuality" && <FactualityBreakdown coverage={coverage} published={story.factualityPublished} />}
        {tab === "ownership" && <OwnershipBreakdown coverage={coverage} />}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  infoRow: { flexDirection: "row", justifyContent: "flex-end", marginBottom: 12 },
  tablist: { flexDirection: "row", gap: 4, borderRadius: radius.md, padding: 4, marginBottom: 12 },
  tab: { flex: 1, minWidth: 0, alignItems: "center", borderRadius: radius.sm, paddingHorizontal: 8, paddingVertical: 6 },
  activeShadow: { shadowColor: "#000", shadowOpacity: 0.05, shadowRadius: 2, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
});
