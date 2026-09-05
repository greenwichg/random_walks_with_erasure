import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { Story } from "@ih/core/domain/types";
import type { TopicGroup } from "@ih/core/logic/home";

import { FollowButton } from "@/components/shared/follow-button";
import { LeadStory } from "@/components/shared/lead-story";
import { SpotCard } from "@/components/shared/spot-card";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { StoryRow } from "@/components/shared/story-row";
import { Button } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { HomeSkeleton } from "./home-skeleton";
import { LocalPulse } from "./local-pulse";
import type { HomeModel } from "./home-model";

/**
 * The home page — one column, composed to the mobile reference from the same shared pieces
 * (LeadStory, StoryRow, SpotCard, BiasStrip, FollowButton):
 *
 *   Briefing → lens tabs → lead → story rows → More stories
 *   → Blind spots → Daily local news → {Topic} news sections
 *
 * It closes on the news: no reader module here (those live on /recommendations and /report). The
 * lens tabs reorder what is already loaded — no tab costs a request. The topic chip strip is chrome
 * (the shell renders it under the masthead on every screen), not page content.
 */
const LENSES = ["top", "latest", "blindspots"] as const;
type Lens = (typeof LENSES)[number];

export function HomeMobile({
  model,
  loading,
  error,
  onRetry,
}: {
  model: HomeModel;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
  const { t, formatCompact, timeAgo } = useTranslation();
  const { palette } = useTheme();
  const { visible, facts, hero, topStories, blindspots, categories, latest } = model;
  const [lens, setLens] = React.useState<Lens>("top");

  const rows = React.useMemo(() => {
    if (lens === "latest") return latest;
    if (lens === "blindspots") return blindspots;
    return topStories;
  }, [lens, topStories, latest, blindspots]);

  return (
    <View>
      {loading && <HomeSkeleton />}
      {error && <ErrorState onRetry={onRetry} />}
      {!loading && !error && visible.length === 0 && (
        <EmptyState icon="newspaper" title={t("home.empty.title")} description={t("home.empty.body")} />
      )}

      {visible.length > 0 && (
        <View style={{ gap: 32 }}>
          {/* Briefing — the day's counted opening statement. */}
          <View style={[styles.briefing, { backgroundColor: palette.card, borderColor: palette.border }]}>
            <Txt display weight="600" size={19} lineHeight={23} tight accessibilityRole="header" style={{ marginBottom: 8 }}>
              {t("home.briefing.title")}
            </Txt>
            <Txt weight="600" size={15} lineHeight={20} tight>
              {facts.blindspotCount > 0
                ? t("home.briefing.blindspotHeadline", {
                    n: formatCompact(facts.blindspotCount),
                    stories: formatCompact(facts.storyCount),
                  })
                : t("home.briefing.balanced")}
            </Txt>
            <Txt size={12} muted style={{ marginTop: 6 }}>
              {t("home.briefing.headline", {
                stories: formatCompact(facts.storyCount),
                publishers: formatCompact(facts.publisherCount),
              })}
            </Txt>
            <View style={styles.briefingFooter}>
              {facts.latestUpdate ? (
                <Txt size={11} muted>
                  {t("home.briefing.updated", { time: timeAgo(facts.latestUpdate) })}
                </Txt>
              ) : (
                <View />
              )}
              <Pressable accessibilityRole="link" onPress={() => navigate("/analyze")} hitSlop={6}>
                <Txt size={11} weight="500" style={{ opacity: 0.8 }}>
                  {t("home.briefing.analyze")}
                </Txt>
              </Pressable>
            </View>
          </View>

          {/* The feed, and the lens over it. */}
          <View accessibilityLabel={t("home.newsStories")}>
            <Tabs
              full
              value={lens}
              onChange={setLens}
              style={{ marginBottom: 12 }}
              items={[
                { value: "top", label: t("home.lens.top") },
                { value: "latest", label: t("home.lens.latest") },
                { value: "blindspots", label: t("home.blindspots.title") },
              ]}
            />

            {lens === "top" && hero && <LeadStory story={hero} style={{ marginBottom: 8 }} />}

            {rows.length > 0 ? (
              <View style={lens === "top" && hero ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.border } : undefined}>
                {rows.map((story: Story, i) => (
                  <StoryRow key={story.id} story={story} size="lg" thumb action last={i === rows.length - 1} />
                ))}
              </View>
            ) : (
              <View style={[styles.emptyNote, { borderColor: palette.border, backgroundColor: alpha(palette.card, 0.4) }]}>
                <Txt size={14} muted align="center">
                  {t("home.empty.body")}
                </Txt>
              </View>
            )}

            <Button
              variant="outline"
              full
              style={{ marginTop: 20 }}
              onPress={() => navigate(lens === "blindspots" ? "/stories?blindspot=any" : "/stories?sort=latest")}
            >
              {t("home.moreStories")}
            </Button>
          </View>

          {/* Blind spots — the product's own signal, as picture cards. */}
          {lens === "top" && blindspots.length > 0 && (
            <View>
              <Txt display weight="600" size={19} lineHeight={23} tight accessibilityRole="header" style={{ marginBottom: 4 }}>
                {t("home.blindspots.title")}
              </Txt>
              <Txt size={12} muted lineHeight={18} style={{ marginBottom: 16 }}>
                {t("home.blindspots.description")}
              </Txt>
              <View style={styles.spots}>
                {blindspots.slice(0, 2).map((story) => (
                  <SpotCard key={story.id} story={story} style={{ flex: 1 }} />
                ))}
              </View>
              <Button variant="outline" full style={{ marginTop: 16 }} onPress={() => navigate("/stories?blindspot=any")}>
                {t("home.blindspots.viewFeed")}
              </Button>
            </View>
          )}

          <LocalPulse />

          {/* {Topic} news — a lead and its rows, with the topic's own follow control. */}
          {categories.slice(0, 2).map((group: TopicGroup) => (
            <TopicSection key={group.topic} group={group} />
          ))}
        </View>
      )}
    </View>
  );
}

function TopicSection({ group }: { group: TopicGroup }) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const [lead, ...rest] = group.stories;
  if (!lead) return null;
  const rows = rest.slice(0, 3);

  return (
    <View style={[styles.topic, { borderTopColor: palette.border }]}>
      <View style={styles.topicHeader}>
        <Txt display weight="700" size={21} lineHeight={25} tight accessibilityRole="header" style={{ flex: 1, minWidth: 0 }}>
          {t("home.topic.section", { topic: group.topic })}
        </Txt>
        <FollowButton topic={group.topic} size="button" />
      </View>
      <LeadStory story={lead} size="md" />
      {rows.length > 0 && (
        <View style={{ marginTop: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.border }}>
          {rows.map((story, i) => (
            <StoryRow key={story.id} story={story} size="md" showTopic={false} thumb last={i === rows.length - 1} />
          ))}
        </View>
      )}
      <Button variant="outline" full style={{ marginTop: 16 }} onPress={() => navigate(`/stories?topic=${encodeURIComponent(group.topic)}`)}>
        {t("common.readMore")}
      </Button>
    </View>
  );
}

const styles = StyleSheet.create({
  briefing: { borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.md, padding: 16 },
  briefingFooter: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, marginTop: 8 },
  emptyNote: { borderWidth: 1, borderStyle: "dashed", borderRadius: radius.md, paddingHorizontal: 16, paddingVertical: 32 },
  spots: { flexDirection: "row", gap: 16 },
  topic: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 24 },
  topicHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 16 },
});
