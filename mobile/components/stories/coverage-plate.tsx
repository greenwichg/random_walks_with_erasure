import * as React from "react";
import { StyleSheet, View } from "react-native";
import Svg, { Defs, LinearGradient, Rect, Stop } from "react-native-svg";

import type { LeanBucket, Story } from "@ih/core/domain/types";
import { monogram } from "@ih/core/logic/placeholder-art";
import { hostIconCandidates, logoCandidates } from "@ih/core/logic/publisher-logo";

import { PublisherLogo } from "@/components/shared/publisher-logo";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

const SIDES: readonly LeanBucket[] = ["left", "center", "right"];
/** Dominant-side lookup order — neutral first, so an even split never tints the plate partisan. */
const WASH_ORDER: readonly LeanBucket[] = ["center", "left", "right"];
const MASTHEAD_CHIPS = 6;

/**
 * The COVERAGE PLATE, masthead form — the designed no-image state for a story page: a full-width
 * coverage strip that closes the hero block, flush under the headline/standfirst/dateline. Kicker
 * (masthead label + time span) with publisher chips, the publisher-count credential (or the thin-side
 * statement on a gap story), and the distribution as one labelled band. A 0% side stays visibly
 * present as a stub, never dropped.
 */
export function CoveragePlate({ story }: { story: Story }) {
  const { t, formatCompact } = useTranslation();
  const { palette } = useTheme();

  const pubCount = story.publisherCount ?? story.publishers?.length ?? 0;
  const share = (s: LeanBucket) => Math.max(0, Math.min(1, story.distribution?.[s] ?? 0));
  const pct = (s: LeanBucket) => Math.round(share(s) * 100);
  const blind = story.blindspotSide;

  const markOf = React.useMemo(() => {
    const m = new Map<string, string[]>();
    for (const c of story.coverage ?? []) {
      if (c.publisher && (c.url || c.publisherLogo) && !m.has(c.publisher)) {
        m.set(c.publisher, logoCandidates(c.publisherLogo, c.publisherLogoFallbacks ?? hostIconCandidates(c.url)));
      }
    }
    return m;
  }, [story.coverage]);
  const chipNames = (story.publishers ?? []).slice(0, MASTHEAD_CHIPS);
  const overflow = Math.max(0, pubCount - chipNames.length);

  const washSide: LeanBucket = blind ?? WASH_ORDER.reduce((a, b) => (share(b) > share(a) ? b : a));

  const spanH = Math.round(story.timeSpanHours ?? 0);
  const span = spanH >= 48 ? t("storyCard.spanDays", { n: Math.round(spanH / 24) }) : spanH >= 1 ? t("storyCard.spanHours", { n: spanH }) : "";
  const kicker = [t("story.coverageMasthead"), span].filter(Boolean).join(" · ");

  const counts = `${t("stories.publishers", { n: formatCompact(pubCount) })} · ${t("stories.articlesCount", { n: formatCompact(story.totalCoverage) })}`;
  const distLabel = SIDES.map((s) => `${t(`filter.${s}`)} ${pct(s)}%`).join(" · ");
  const aria = `${
    blind ? `${t("storyCard.thinOn", { side: t(`filter.${blind}`).toLowerCase() })} — ${t("storyCard.ratedShare", { pct: pct(blind) })}. ` : ""
  }${counts}. ${distLabel}`;

  return (
    <View accessible accessibilityRole="image" accessibilityLabel={aria} style={[styles.plate, { borderTopColor: palette.border, backgroundColor: palette.card }]}>
      {/* Tinted by the story's own data — the thin side of a detected gap, else the dominant side. */}
      <Svg style={StyleSheet.absoluteFill} width="100%" height="100%">
        <Defs>
          <LinearGradient id="hv-plate-wash" x1="0" y1="0" x2="0.7" y2="1">
            <Stop offset="0" stopColor={palette[washSide]} stopOpacity={blind ? 0.1 : 0.08} />
            <Stop offset="0.7" stopColor={palette[washSide]} stopOpacity={0} />
          </LinearGradient>
        </Defs>
        <Rect width="100%" height="100%" fill="url(#hv-plate-wash)" />
      </Svg>

      <View style={styles.top}>
        {kicker ? (
          <Txt size={11} weight="600" uppercase tracking={0.6} muted numberOfLines={1} style={{ flexShrink: 1 }}>
            {kicker}
          </Txt>
        ) : null}
        {chipNames.length > 0 && (
          <View style={styles.chips} accessibilityElementsHidden>
            {chipNames.map((p, i) => {
              const icons = markOf.get(p) ?? [];
              return (
                <View key={p} style={[styles.chip, { borderColor: palette.card, backgroundColor: palette.muted, marginLeft: i === 0 ? 0 : -8 }]}>
                  <PublisherLogo
                    logo={icons[0]}
                    fallbacks={icons.slice(1)}
                    sizePx={20}
                    fallbackNode={
                      <Txt size={9} weight="700" muted lineHeight={11}>
                        {monogram(p)}
                      </Txt>
                    }
                  />
                </View>
              );
            })}
            {overflow > 0 && (
              <View style={[styles.chip, styles.overflow, { borderColor: palette.border, backgroundColor: palette.card, marginLeft: -8 }]}>
                <Txt size={9} weight="600" muted lineHeight={11}>
                  +{formatCompact(overflow)}
                </Txt>
              </View>
            )}
          </View>
        )}
      </View>

      {blind ? (
        <View>
          <View style={styles.inline}>
            <Icon name="eye-off" size={16} color={palette[blind]} />
            <Txt size={14} weight="600" color={palette[blind]}>
              {t("storyCard.thinOn", { side: t(`filter.${blind}`).toLowerCase() })}
            </Txt>
          </View>
          <Txt size={12} muted style={{ marginTop: 2 }}>
            {t("storyCard.ratedShare", { pct: pct(blind) })} · {t("stories.publishers", { n: formatCompact(pubCount) })}
          </Txt>
        </View>
      ) : (
        <View style={styles.credential}>
          <Txt display weight="700" size={48} lineHeight={48} tight tabular>
            {formatCompact(pubCount)}
          </Txt>
          <Txt size={12} muted lineHeight={15}>
            {t("storyCard.publishersLabel")}
            {"\n"}
            {t("stories.articlesCount", { n: formatCompact(story.totalCoverage) })}
          </Txt>
        </View>
      )}

      <View style={styles.band} accessibilityElementsHidden>
        {SIDES.map((s) => {
          const sh = share(s);
          const letter = t(`filter.${s}`).charAt(0);
          if (sh <= 0) {
            return (
              <View key={s} style={[styles.stub, { borderColor: alpha(palette.mutedForeground, 0.3) }]}>
                <Txt size={10.5} weight="600" muted>
                  {letter} 0
                </Txt>
              </View>
            );
          }
          return (
            <View key={s} style={[styles.segment, { flexGrow: sh, backgroundColor: palette[s] }]}>
              {sh >= 0.14 && (
                <Txt size={10.5} weight="600" tabular color={palette.card}>
                  {letter} {pct(s)}
                </Txt>
              )}
            </View>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  plate: { minHeight: 176, borderTopWidth: StyleSheet.hairlineWidth, paddingHorizontal: 20, paddingVertical: 16, justifyContent: "space-between", gap: 8, overflow: "hidden" },
  top: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: 8 },
  chips: { flexDirection: "row", alignItems: "center", paddingLeft: 4 },
  chip: { width: 24, height: 24, borderRadius: radius.pill, borderWidth: 2, alignItems: "center", justifyContent: "center", overflow: "hidden" },
  overflow: { borderStyle: "dashed" },
  inline: { flexDirection: "row", alignItems: "center", gap: 6 },
  credential: { flexDirection: "row", alignItems: "flex-end", gap: 10 },
  band: { flexDirection: "row", height: 32, gap: 2, borderRadius: radius.md, overflow: "hidden" },
  segment: { flexBasis: 0, alignItems: "center", justifyContent: "center" },
  stub: { minWidth: 34, flexGrow: 0.06, flexBasis: 0, alignItems: "center", justifyContent: "center", borderWidth: 1, borderStyle: "dashed", borderRadius: radius.xs },
});
