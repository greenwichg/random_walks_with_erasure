import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { LeanBucket } from "@ih/core/domain/types";
import type { BiasGroups, OutletMark } from "@ih/core/logic/bias-distribution";
import { BIAS_BUCKETS, dominantBucket, splitAtCap } from "@ih/core/logic/bias-distribution";

import { OutletAvatar } from "@/components/shared/outlet-avatar";
import { useReadArticleAction } from "@/components/shared/read-article-button";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

const OPENED_FROM = "story-bias";
const CHIP_PX = 48;
const UNTRACKED_PX = 40;
const ROW_PX = 32;
const COLUMN_CHIPS = 5;
const UNTRACKED_CHIPS = 8;

type PanelKey = LeanBucket | "untracked";

/**
 * The bias-distribution visual: the headline share, the L/C/R spectrum counted in OUTLETS, one slim
 * vertical capsule of outlet marks per side, and the untracked strip for outlets the registry
 * doesn't rate. A side with zero outlets keeps its capsule as a stub; a capsule that overflows ends
 * in a `+N` chip that opens the N it names in a sheet. Chips open the outlet's newest article on
 * this story through the shared Read pipeline.
 */
export function BiasDistribution({ groups }: { groups: BiasGroups }) {
  const { t, formatCompact } = useTranslation();
  const { palette } = useTheme();
  const [panel, setPanel] = React.useState<PanelKey | null>(null);
  const dominant = dominantBucket(groups);
  if (groups.ratedCount === 0 && groups.untracked.length === 0) return null;

  const labelOf = (key: PanelKey) => (key === "untracked" ? t("story.untrackedBias") : t(`filter.${key}`));
  const colorOf = (key: PanelKey) => (key === "untracked" ? palette.mutedForeground : palette[key]);
  const hiddenOf = (key: PanelKey) =>
    key === "untracked" ? splitAtCap(groups.untracked, UNTRACKED_CHIPS).hidden : splitAtCap(groups.buckets[key], COLUMN_CHIPS).hidden;
  const openOutlets = panel ? hiddenOf(panel) : [];

  return (
    <View>
      {dominant && (
        <View style={styles.summary}>
          <View style={[styles.dot, { backgroundColor: palette[dominant.bucket] }]} />
          <Txt size={12} weight="500" muted>
            {t("story.biasSummary", { pct: dominant.pct, side: t(`filter.${dominant.bucket}`) })}
          </Txt>
        </View>
      )}

      {groups.ratedCount > 0 && (
        <>
          <SpectrumBar
            distribution={{ left: groups.buckets.left.length, center: groups.buckets.center.length, right: groups.buckets.right.length }}
            height={10}
          />
          <View style={styles.capsules}>
            {BIAS_BUCKETS.map((bucket) => {
              const outlets = groups.buckets[bucket];
              const { shown, hidden } = splitAtCap(outlets, COLUMN_CHIPS);
              return (
                <View key={bucket} style={styles.column}>
                  <View
                    accessibilityLabel={`${t(`filter.${bucket}`)} (${outlets.length})`}
                    style={[
                      styles.capsule,
                      outlets.length > 0
                        ? { backgroundColor: alpha(palette[bucket], 0.12) }
                        : { borderWidth: 1, borderStyle: "dashed", borderColor: alpha(palette.mutedForeground, 0.35) },
                    ]}
                  >
                    {shown.map((o) => (
                      <OutletChip key={o.publisher} outlet={o} size={CHIP_PX} />
                    ))}
                    {hidden.length > 0 && (
                      <OverflowChip label={`+${formatCompact(hidden.length)}`} onPress={() => setPanel(bucket)} size={CHIP_PX} />
                    )}
                  </View>
                </View>
              );
            })}
          </View>
        </>
      )}

      {groups.untracked.length > 0 && (
        <View style={[styles.untracked, { borderTopColor: palette.border }]}>
          <Txt size={11} weight="600" uppercase tracking={0.6} muted>
            {t("story.untrackedBias")}
          </Txt>
          <View style={styles.strip}>
            {splitAtCap(groups.untracked, UNTRACKED_CHIPS).shown.map((o) => (
              <OutletChip key={o.publisher} outlet={o} size={UNTRACKED_PX} />
            ))}
            {groups.untracked.length > UNTRACKED_CHIPS && (
              <OverflowChip
                label={`+${formatCompact(groups.untracked.length - UNTRACKED_CHIPS)}`}
                onPress={() => setPanel("untracked")}
                size={UNTRACKED_PX}
              />
            )}
          </View>
        </View>
      )}

      <BottomSheet
        open={panel !== null}
        onClose={() => setPanel(null)}
        title={
          <View style={styles.summary}>
            <View style={[styles.dot, { backgroundColor: panel ? colorOf(panel) : undefined }]} />
            <Txt size={14} weight="600">
              {panel ? labelOf(panel) : ""}
            </Txt>
          </View>
        }
        description={t("story.moreOutlets", { n: openOutlets.length })}
      >
        {openOutlets.map((o) => (
          <HiddenOutletRow
            key={o.publisher}
            outlet={o}
            label={panel === "untracked" ? t("lean.unknown") : panel ? labelOf(panel) : ""}
            color={panel ? colorOf(panel) : undefined}
            onNavigate={() => setPanel(null)}
          />
        ))}
      </BottomSheet>
    </View>
  );
}

function OutletChip({ outlet, size }: { outlet: OutletMark; size: number }) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const { actionable, opened, open } = useReadArticleAction({ url: outlet.url, headline: outlet.headline }, OPENED_FROM);
  if (!actionable) {
    return (
      <View accessible accessibilityLabel={outlet.publisher}>
        <OutletAvatar outlet={outlet} size={size} />
      </View>
    );
  }
  const label = t("story.openArticleFrom", { publisher: outlet.publisher, headline: outlet.headline ?? "" });
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ selected: opened }} onPress={open}>
      <OutletAvatar outlet={outlet} size={size} style={opened ? { borderWidth: 2, borderColor: alpha(palette.positive, 0.6) } : undefined} />
    </Pressable>
  );
}

function HiddenOutletRow({ outlet, label, color, onNavigate }: { outlet: OutletMark; label: string; color?: string; onNavigate: () => void }) {
  const { palette } = useTheme();
  const { actionable, opened, open } = useReadArticleAction({ url: outlet.url, headline: outlet.headline }, OPENED_FROM);
  return (
    <Pressable
      accessibilityRole={actionable ? "button" : "link"}
      accessibilityState={{ selected: opened }}
      onPress={() => {
        if (actionable) open();
        else {
          onNavigate();
          navigate(`/publishers/${encodeURIComponent(outlet.publisher)}`);
        }
      }}
      style={({ pressed }) => [styles.hiddenRow, pressed && { backgroundColor: alpha(palette.accent, 0.5) }]}
    >
      <OutletAvatar outlet={outlet} size={ROW_PX} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <Txt size={14} weight="500" numberOfLines={1}>
          {outlet.publisher}
        </Txt>
        {outlet.headline && (
          <Txt size={12} muted numberOfLines={1}>
            {outlet.headline}
          </Txt>
        )}
      </View>
      <Txt size={11} weight="500" color={color}>
        {label}
      </Txt>
    </Pressable>
  );
}

/** The `+N` chip — the house's own control, not a publisher's mark: dashed, themed, never white. */
function OverflowChip({ label, onPress, size }: { label: string; onPress: () => void; size: number }) {
  const { palette } = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      style={({ pressed }) => [
        styles.overflow,
        { width: size, height: size, borderColor: palette.border, backgroundColor: pressed ? palette.muted : palette.card },
      ]}
    >
      <Txt size={Math.round(size * 0.26)} weight="600" muted>
        {label}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  summary: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 10 },
  dot: { width: 6, height: 6, borderRadius: radius.pill },
  capsules: { flexDirection: "row", gap: 8, marginTop: 12 },
  column: { flex: 1, alignItems: "center" },
  capsule: { width: 56, minHeight: 64, alignItems: "center", justifyContent: "flex-start", gap: 8, borderRadius: radius.pill, paddingHorizontal: 4, paddingVertical: 8 },
  untracked: { marginTop: 12, borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 12 },
  strip: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8, marginTop: 8 },
  hiddenRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 8, paddingVertical: 8, borderRadius: radius.md },
  overflow: { alignItems: "center", justifyContent: "center", borderRadius: radius.pill, borderWidth: 1, borderStyle: "dashed" },
});
