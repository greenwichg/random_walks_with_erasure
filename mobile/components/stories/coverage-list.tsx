import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { LeanBucket, Register, StoryCoverage } from "@ih/core/domain/types";
import { collapseConsecutive } from "@ih/core/logic/coverage-groups";
import { monogram } from "@ih/core/logic/placeholder-art";
import { hostIconCandidates, logoCandidates } from "@ih/core/logic/publisher-logo";
import { splitCoverage } from "@ih/core/logic/story-attached";

import { LeanBadge } from "@/components/shared/article-badges";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { Button } from "@/components/ui/button";
import { FilterChip } from "@/components/ui/filter-chip";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

const LEAN_FILTERS: ("all" | LeanBucket)[] = ["all", "left", "center", "right"];
/** Rows shown before "Read more" — an editorial choice: a legible sample, not the whole cluster. */
const INITIAL = 6;
/** What one "Read more" reveals. */
const STEP = 15;

/**
 * The story's article coverage as a FILTERABLE list — WHO first (publisher mark + name, the lean
 * badge as counterweight), the headline as the row's one big line, then a quiet metadata line whose
 * actions are always present (touch has no hover). Consecutive updates by one outlet collapse to
 * the newest row plus a "+N earlier" expander. Attached Tier B rows render as their own labeled
 * group below, only with the filters at rest.
 */
export function CoverageList({ coverage }: { coverage: StoryCoverage[] }) {
  const { t, formatCompact } = useTranslation();
  const { palette } = useTheme();
  const [lean, setLean] = React.useState<"all" | LeanBucket>("all");
  const [register, setRegister] = React.useState<"all" | Register>("all");
  const [oldestFirst, setOldestFirst] = React.useState(false);

  const { panel, attached } = React.useMemo(() => splitCoverage(coverage), [coverage]);

  const leanCounts = React.useMemo(() => {
    const counts: Record<string, number> = { left: 0, center: 0, right: 0 };
    for (const row of panel) if (row.leanBucket) counts[row.leanBucket] = (counts[row.leanBucket] ?? 0) + 1;
    return counts;
  }, [panel]);

  const registers = React.useMemo(() => {
    const present = new Set<Register>();
    for (const row of panel) if (row.register) present.add(row.register);
    return (["reporting", "opinion", "mixed"] as Register[]).filter((r) => present.has(r));
  }, [panel]);

  const rows = React.useMemo(() => {
    const filtered = panel.filter(
      (row) => (lean === "all" || row.leanBucket === lean) && (register === "all" || row.register === register),
    );
    return filtered.sort((a, b) =>
      oldestFirst ? (a.publishedAt ?? "").localeCompare(b.publishedAt ?? "") : (b.publishedAt ?? "").localeCompare(a.publishedAt ?? ""),
    );
  }, [panel, lean, register, oldestFirst]);

  const groups = React.useMemo(() => collapseConsecutive(rows), [rows]);
  const attachedRows = React.useMemo(
    () =>
      [...attached].sort((a, b) =>
        oldestFirst ? (a.publishedAt ?? "").localeCompare(b.publishedAt ?? "") : (b.publishedAt ?? "").localeCompare(a.publishedAt ?? ""),
      ),
    [attached, oldestFirst],
  );
  const showAttached = attachedRows.length > 0 && lean === "all" && register === "all";

  const [visible, setVisible] = React.useState(INITIAL);
  const [openRuns, setOpenRuns] = React.useState<Set<string>>(() => new Set());
  React.useEffect(() => {
    setVisible(INITIAL);
    setOpenRuns(new Set());
  }, [lean, register, oldestFirst]);

  const shown = groups.slice(0, visible);
  const hasMore = visible < groups.length;
  const shownArticles = shown.reduce((n, g) => n + 1 + g.rest.length, 0);

  return (
    <View>
      <View style={styles.toolbar} accessibilityRole="toolbar" accessibilityLabel={t("stories.coverageAcross")}>
        {LEAN_FILTERS.map((value) => (
          <FilterChip
            key={value}
            active={lean === value}
            onPress={() => setLean(value)}
            label={value === "all" ? t("rec.filter.all") : t(`filter.${value}`)}
            count={value === "all" ? panel.length : leanCounts[value]}
          />
        ))}
        {registers.length > 1 && (
          <>
            <View style={[styles.sep, { backgroundColor: palette.border }]} />
            {registers.map((value) => (
              <FilterChip
                key={value}
                active={register === value}
                onPress={() => setRegister(register === value ? "all" : value)}
                label={t(`register.${value}`)}
              />
            ))}
          </>
        )}
        <View style={[styles.sep, { backgroundColor: palette.border }]} />
        <FilterChip active={false} onPress={() => setOldestFirst((v) => !v)} label={oldestFirst ? t("filter.oldest") : t("filter.newest")} />
      </View>

      {rows.length === 0 ? (
        <View style={[styles.noMatch, { borderColor: palette.border, backgroundColor: alpha(palette.card, 0.4) }]}>
          <Txt size={14} muted align="center">
            {t("story.noMatches")}
          </Txt>
          <Button
            variant="outline"
            size="sm"
            style={{ marginTop: 12, alignSelf: "center" }}
            onPress={() => {
              setLean("all");
              setRegister("all");
            }}
          >
            {t("common.reset")}
          </Button>
        </View>
      ) : (
        <View>
          {shown.map((group, i) => {
            const runKey = `${group.lead.publisher}|${group.lead.publishedAt}`;
            const open = openRuns.has(runKey);
            return (
              <View key={`${runKey}-${i}`} style={[styles.group, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.border }]}>
                <CoverageRow row={group.lead} badge={<LeanBadge lean={group.lead.lean} bucket={group.lead.leanBucket} />} />
                {group.rest.length > 0 && !open && (
                  <Pressable
                    accessibilityRole="button"
                    onPress={() => setOpenRuns((s) => new Set(s).add(runKey))}
                    style={({ pressed }) => [styles.expander, pressed && { backgroundColor: palette.muted }]}
                  >
                    <Icon name="chevron-down" size={14} color={palette.mutedForeground} />
                    <Txt size={12} weight="500" muted>
                      {t("story.moreFrom", { n: formatCompact(group.rest.length), publisher: group.lead.publisher })}
                    </Txt>
                  </Pressable>
                )}
                {open &&
                  group.rest.map((row, j) => (
                    <View key={`${row.publishedAt}-${j}`} style={[styles.nested, { borderLeftColor: palette.border }]}>
                      <CoverageRow row={row} badge={<LeanBadge lean={row.lean} bucket={row.leanBucket} />} />
                    </View>
                  ))}
              </View>
            );
          })}
        </View>
      )}

      {hasMore && (
        <Button variant="outline" full iconRight="chevron-down" style={{ marginTop: 16 }} onPress={() => setVisible((v) => v + STEP)}>
          {t("story.readMore", { n: formatCompact(groups.length - visible) })}
        </Button>
      )}
      <Txt size={12} muted style={{ marginTop: 8 }}>
        {formatCompact(shownArticles)} / {formatCompact(rows.length)}
      </Txt>

      {showAttached && (
        <View style={[styles.attached, { borderColor: palette.border, backgroundColor: alpha(palette.card, 0.4) }]}>
          <Txt size={11} weight="600" uppercase tracking={0.6} muted>
            {t("story.beyondPanel", { n: formatCompact(attachedRows.length) })}
          </Txt>
          <Txt size={12} muted style={{ marginTop: 4 }}>
            {t("story.beyondPanelNote")}
          </Txt>
          <View style={{ marginTop: 4 }}>
            {attachedRows.map((row, i) => (
              <View key={`${row.publisher}-${row.publishedAt}-${i}`} style={[styles.group, i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.border }]}>
                <CoverageRow
                  row={row}
                  badge={
                    <View style={[styles.beyondBadge, { borderColor: palette.border }]}>
                      <Txt size={10} weight="500" muted lineHeight={14}>
                        {t("story.beyondPanelBadge")}
                      </Txt>
                    </View>
                  }
                />
              </View>
            ))}
          </View>
        </View>
      )}
    </View>
  );
}

/** One coverage row: publisher pill + its badge counterweight, the headline, one quiet meta line with the actions. */
function CoverageRow({ row, badge }: { row: StoryCoverage; badge: React.ReactNode }) {
  const { t, timeAgo } = useTranslation();
  const { palette } = useTheme();
  const icons = logoCandidates(row.publisherLogo, row.publisherLogoFallbacks ?? hostIconCandidates(row.url));
  return (
    <View style={styles.row}>
      <View style={styles.rowTop}>
        <Pressable
          accessibilityRole="link"
          onPress={() => navigate(`/publishers/${encodeURIComponent(row.publisher)}`)}
          style={({ pressed }) => [styles.pill, { backgroundColor: pressed ? palette.muted : alpha(palette.muted, 0.7) }]}
        >
          <View style={[styles.pillIcon, { backgroundColor: palette.card }]}>
            <PublisherLogo
              logo={icons[0]}
              fallbacks={icons.slice(1)}
              sizePx={16}
              fallbackNode={
                <Txt size={8} weight="700" muted lineHeight={10}>
                  {monogram(row.publisher)}
                </Txt>
              }
            />
          </View>
          <Txt size={12} weight="500" numberOfLines={1} style={{ flexShrink: 1 }}>
            {row.publisher}
          </Txt>
        </Pressable>
        {badge}
      </View>

      <Txt display weight="600" size={15} lineHeight={20} tight numberOfLines={2} style={{ marginTop: 8 }}>
        {row.headline}
      </Txt>

      <View style={styles.meta}>
        {row.publishedAt ? (
          <Txt size={12} muted>
            {timeAgo(row.publishedAt)}
          </Txt>
        ) : null}
        {row.register && <Txt size={12} muted>{`· ${t(`register.${row.register}`)}`}</Txt>}
        <View style={{ flex: 1 }} />
        <View style={styles.actions}>
          <ReadArticleButton article={{ url: row.url, headline: row.headline }} openedFrom="stories" variant="soft" compact />
          {row.url && (
            <SaveButton
              compact
              article={{ id: row.url, url: row.url, headline: row.headline, publisher: row.publisher, publishedAt: row.publishedAt }}
            />
          )}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  toolbar: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 12 },
  sep: { width: StyleSheet.hairlineWidth, height: 16 },
  noMatch: { borderWidth: 1, borderStyle: "dashed", borderRadius: radius.lg, paddingHorizontal: 16, paddingVertical: 32 },
  group: { paddingVertical: 4 },
  row: { paddingVertical: 14 },
  rowTop: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 8 },
  pill: { flexDirection: "row", alignItems: "center", gap: 6, borderRadius: radius.pill, paddingVertical: 4, paddingLeft: 4, paddingRight: 10, maxWidth: "100%" },
  pillIcon: { width: 18, height: 18, borderRadius: radius.pill, alignItems: "center", justifyContent: "center", overflow: "hidden" },
  meta: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6, minHeight: 28 },
  actions: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 0 },
  expander: { flexDirection: "row", alignItems: "center", gap: 4, alignSelf: "flex-start", borderRadius: radius.sm, paddingHorizontal: 8, paddingVertical: 4, marginBottom: 8 },
  nested: { borderLeftWidth: 2, paddingLeft: 12 },
  attached: { marginTop: 20, borderWidth: 1, borderStyle: "dashed", borderRadius: radius.lg, paddingHorizontal: 16, paddingVertical: 12 },
  beyondBadge: { borderWidth: 1, borderStyle: "dashed", borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 2 },
});
