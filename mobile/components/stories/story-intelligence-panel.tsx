import * as React from "react";
import { StyleSheet, View } from "react-native";

import type { StoryLifecycle, StoryMomentum, StoryTimelineEventType } from "@ih/core/domain/types";
import { condenseTimeline } from "@ih/core/logic/story-timeline";

import { Icon, type IconName } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { Txt } from "@/components/ui/text";
import { tw } from "@/design/tailwind";
import { alpha, radius } from "@/design/tokens";
import { useStoryIntelligence } from "@/lib/hooks";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

const TIMELINE_ICON: Record<StoryTimelineEventType, IconName> = {
  first_report: "flag",
  publisher_join: "user-plus",
  perspective_expansion: "scale",
  milestone: "milestone",
  latest: "clock",
};

/** Condensed rows shown before "Show all" expands the log. */
const TIMELINE_LIMIT = 4;
/** How many publisher chips a grouped join row names before the "+n" overflow chip. */
const GROUP_CHIP_LIMIT = 4;

/**
 * Story Intelligence — lifecycle / momentum, "new since your last visit", coverage alerts, a
 * collapsed timeline and coverage statistics for one event. Renders nothing if the engine cannot
 * supply it. Read-only. On the phone it lives inside the collapsible panel, which supplies the
 * heading and the surface — so this is the `headless` body.
 */
export function StoryIntelligencePanel({ storyId }: { storyId: string }) {
  const { t, timeAgo, formatDate, lang } = useTranslation();
  const { palette, scheme } = useTheme();
  const { data, isLoading } = useStoryIntelligence(storyId);
  const [expanded, setExpanded] = React.useState(false);

  const rows = React.useMemo(() => condenseTimeline(data?.timeline ?? []), [data?.timeline]);

  if (isLoading) return <Skeleton height={224} />;
  if (!data) return null;

  const dark = scheme === "dark";
  const lifecycleStyle: Record<StoryLifecycle, { bg: string; fg: string; ring: string }> = {
    Breaking: { bg: alpha(tw.red500, 0.12), fg: dark ? tw.red400 : tw.red600, ring: alpha(tw.red500, 0.2) },
    Developing: { bg: alpha(tw.amber500, 0.12), fg: dark ? tw.amber400 : tw.amber600, ring: alpha(tw.amber500, 0.2) },
    Mature: { bg: palette.muted, fg: alpha(palette.foreground, 0.7), ring: palette.border },
    Archived: { bg: palette.muted, fg: palette.mutedForeground, ring: palette.border },
  };
  const momentumMeta: Record<StoryMomentum["state"], { icon: IconName; color: string }> = {
    Growing: { icon: "trending-up", color: dark ? tw.emerald400 : tw.emerald600 },
    Stable: { icon: "minus", color: palette.mutedForeground },
    Declining: { icon: "trending-down", color: dark ? tw.slate400 : tw.slate500 },
  };

  const visibleRows = expanded ? rows : rows.slice(0, TIMELINE_LIMIT);
  const hiddenCount = rows.length - visibleRows.length;
  const { lifecycle, momentum, newSinceLastVisit: nsv, alerts } = data;
  const cs = data.coverageStatistics;
  const lc = lifecycleStyle[lifecycle] ?? lifecycleStyle.Mature;
  const mo = momentumMeta[momentum.state] ?? momentumMeta.Stable;

  const fmtDate = (iso?: string) => (iso ? formatDate(iso, { month: "short", day: "numeric" }) : "");
  const fmtTime = (iso?: string) => {
    if (!iso) return "";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString(lang, { hour: "numeric", minute: "2-digit" });
  };

  return (
    <View>
      {/* Status: lifecycle + momentum. */}
      <View style={styles.status}>
        <View style={[styles.pill, { backgroundColor: lc.bg, borderColor: lc.ring }]}>
          <Txt size={12} weight="500" color={lc.fg} lineHeight={16}>
            {t(`storyIntel.lifecycle.${lifecycle}`)}
          </Txt>
        </View>
        <View style={styles.momentum}>
          <Icon name={mo.icon} size={14} color={mo.color} />
          <Txt size={12} weight="500" color={mo.color}>
            {t(`storyIntel.momentum.${momentum.state}`)}
          </Txt>
          {momentum.newPublishers > 0 && (
            <Txt size={12} muted>{`· ${t("storyIntel.plusNew", { n: momentum.newPublishers })}`}</Txt>
          )}
        </View>
      </View>

      {/* New since your last visit */}
      {nsv.count > 0 && (
        <View style={[styles.newBox, { borderColor: alpha(palette.primary, 0.2), backgroundColor: alpha(palette.primary, 0.05) }]}>
          <View style={styles.inline}>
            <Icon name="sparkles" size={16} color={palette.primary} />
            <Txt size={14} weight="500" color={palette.primary}>
              {t("storyIntel.newArticles", { n: nsv.count })}
            </Txt>
            {nsv.lastVisited && <Txt size={14} muted>{`· ${t("storyIntel.lastRead", { time: timeAgo(nsv.lastVisited) })}`}</Txt>}
          </View>
          {(nsv.publishers.length > 0 || nsv.perspectives.length > 0) && (
            <View style={[styles.chips, { marginTop: 8 }]}>
              {nsv.publishers.map((p) => (
                <View key={p} style={[styles.chip, { backgroundColor: palette.background, borderColor: palette.border }]}>
                  <Txt size={12} lineHeight={16}>{p}</Txt>
                </View>
              ))}
              {nsv.perspectives.map((b) => (
                <View key={b} style={[styles.chip, { borderColor: palette[b] }]}>
                  <Txt size={12} weight="500" color={palette[b]} lineHeight={16}>
                    {t("storyIntel.newPerspective", { label: t(`filter.${b}`) })}
                  </Txt>
                </View>
              ))}
            </View>
          )}
        </View>
      )}

      {/* Coverage alerts (informational) */}
      {alerts.length > 0 && (
        <View style={{ marginTop: 16, gap: 6 }}>
          {alerts.map((a, i) => (
            <View key={`${a.type}-${i}`} style={styles.alert}>
              <Icon name="bell" size={14} color={tw.amber500} style={{ marginTop: 2 }} />
              <Txt size={14} muted style={{ flex: 1 }}>
                {a.message}
              </Txt>
            </View>
          ))}
        </View>
      )}

      {/* Coverage statistics */}
      <View style={styles.stats}>
        <Stat label={t("storyIntel.perDay")} value={cs.coverageVelocityPerDay.toFixed(1)} />
        <Stat label={t("storyIntel.recentVsPrior")} value={`${cs.coverageGrowth.recent}/${cs.coverageGrowth.prior}`} />
        <Stat
          label={t("storyIntel.span")}
          value={cs.coverageDurationHours >= 24 ? `${(cs.coverageDurationHours / 24).toFixed(1)}d` : `${Math.round(cs.coverageDurationHours)}h`}
        />
      </View>

      {/* THE COVERAGE TIMELINE: time leads in a fixed column, one continuous spine with every node
          centred on it, chips hanging off the label. */}
      {rows.length > 0 && (
        <View style={{ marginTop: 20 }}>
          <View style={[styles.inline, { marginBottom: 12 }]}>
            <Icon name="gauge" size={14} color={palette.mutedForeground} />
            <Txt size={12} weight="600" uppercase tracking={0.5} muted>
              {t("storyIntel.coverageTimeline")}
            </Txt>
          </View>
          <View style={styles.timeline}>
            <View style={[styles.spine, { backgroundColor: palette.border }]} />
            {visibleRows.map((row, i) => {
              if (row.kind === "day") {
                return (
                  <View key={`day-${row.iso}`} style={[styles.dayRow, i > 0 && { paddingTop: 8 }]}>
                    <Txt size={11} weight="600" uppercase tracking={0.6} muted style={{ backgroundColor: palette.card, paddingRight: 6 }}>
                      {fmtDate(row.iso)}
                    </Txt>
                    <View style={[styles.dayRule, { backgroundColor: palette.border }]} />
                  </View>
                );
              }
              if (row.kind === "joins") {
                const shown = row.publishers.slice(0, GROUP_CHIP_LIMIT);
                const overflow = row.publishers.length - shown.length;
                return (
                  <View key={`joins-${row.date}-${i}`} style={styles.row}>
                    <TimelineNode icon="user-plus" />
                    <View style={styles.grid}>
                      <Txt size={11} weight="500" tabular muted style={styles.time}>
                        {fmtTime(row.date)}
                      </Txt>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <Txt size={13} lineHeight={17}>
                          {t("storyIntel.joinedGroup", { n: row.publishers.length })}
                        </Txt>
                        <View style={[styles.chips, { marginTop: 6 }]}>
                          {shown.map((p) => (
                            <View key={p} style={[styles.chip, { backgroundColor: alpha(palette.muted, 0.7), borderColor: palette.border }]}>
                              <Txt size={11} lineHeight={15}>{p}</Txt>
                            </View>
                          ))}
                          {overflow > 0 && (
                            <View style={[styles.chip, { backgroundColor: alpha(palette.muted, 0.7), borderColor: palette.border }]}>
                              <Txt size={11} muted tabular lineHeight={15}>{`+${overflow}`}</Txt>
                            </View>
                          )}
                        </View>
                      </View>
                    </View>
                  </View>
                );
              }
              const e = row.event;
              const accent = e.type === "perspective_expansion" && e.perspective ? palette[e.perspective] : undefined;
              return (
                <View key={`${e.type}-${e.date}-${i}`} style={styles.row}>
                  <TimelineNode icon={TIMELINE_ICON[e.type] ?? "arrow-right"} accent={accent} />
                  <View style={styles.grid}>
                    <Txt size={11} weight="500" tabular muted style={styles.time}>
                      {fmtTime(e.date)}
                    </Txt>
                    <Txt size={13} lineHeight={17} weight={e.type === "first_report" ? "500" : "400"} style={{ flex: 1 }}>
                      {e.label}
                    </Txt>
                  </View>
                </View>
              );
            })}
          </View>

          {(hiddenCount > 0 || expanded) && (
            <Txt
              size={12}
              weight="500"
              color={palette.primary}
              accessibilityRole="button"
              onPress={() => setExpanded((v) => !v)}
              style={{ marginTop: 12 }}
            >
              {expanded ? `▴ ${t("storyIntel.showFewerEvents")}` : `▾ ${t("storyIntel.showAllEvents", { n: rows.length })}`}
            </Txt>
          )}
        </View>
      )}
    </View>
  );
}

/** A node on the spine: an opaque disc so the thread reads as passing behind it. */
function TimelineNode({ icon, accent }: { icon: IconName; accent?: string }) {
  const { palette } = useTheme();
  return (
    <View style={[styles.node, { backgroundColor: palette.card, borderColor: palette.border }]}>
      <Icon name={icon} size={10} color={accent ?? palette.mutedForeground} />
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  const { palette } = useTheme();
  return (
    <View style={[styles.stat, { borderColor: palette.border, backgroundColor: alpha(palette.background, 0.5) }]}>
      <Txt size={11} uppercase tracking={0.5} muted>
        {label}
      </Txt>
      <Txt weight="600" tabular style={{ marginTop: 2 }}>
        {value}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  status: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8, marginTop: 4 },
  pill: { borderRadius: radius.pill, borderWidth: 1, paddingHorizontal: 8, paddingVertical: 2 },
  momentum: { flexDirection: "row", alignItems: "center", gap: 4 },
  newBox: { marginTop: 16, borderWidth: 1, borderRadius: radius.md, padding: 12 },
  inline: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" },
  chips: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6 },
  chip: { borderRadius: radius.pill, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 8, paddingVertical: 2 },
  alert: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  stats: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 16 },
  stat: { flexBasis: "47%", flexGrow: 1, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 8 },
  timeline: { position: "relative", gap: 12 },
  spine: { position: "absolute", left: 8, top: 8, bottom: 8, width: StyleSheet.hairlineWidth },
  dayRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  dayRule: { flex: 1, height: StyleSheet.hairlineWidth },
  row: { position: "relative", paddingLeft: 28 },
  node: { position: "absolute", left: 0, top: 0, width: 16, height: 16, borderRadius: radius.pill, borderWidth: StyleSheet.hairlineWidth, alignItems: "center", justifyContent: "center" },
  grid: { flexDirection: "row", alignItems: "flex-start", gap: 12 },
  time: { width: 56, flexShrink: 0, lineHeight: 17 },
});
