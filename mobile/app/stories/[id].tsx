import { useLocalSearchParams } from "expo-router";
import * as React from "react";
import { Pressable, Share, StyleSheet, View } from "react-native";

import { framingComparison } from "@ih/core/logic/framing";
import { splitCoverage } from "@ih/core/logic/story-attached";

import { Screen } from "@/components/layout/screen";
import { CardImage } from "@/components/shared/card-image";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { StoryBreakdown } from "@/components/stories/breakdown/story-breakdown";
import { CoverageList } from "@/components/stories/coverage-list";
import { CoveragePlate } from "@/components/stories/coverage-plate";
import { FramingComparison } from "@/components/stories/framing-comparison";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { MAX_CARDS, SimilarStories } from "@/components/stories/similar-stories";
import { StoryIntelligencePanel } from "@/components/stories/story-intelligence-panel";
import { StorySection, StorySections } from "@/components/stories/story-section";
import { StoryTopics } from "@/components/stories/story-topics";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { track, urlHost } from "@/lib/analytics";
import { config } from "@/lib/config";
import { useSimilarStories, useStory } from "@/lib/hooks";
import { back } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * Story Details — the mobile web's story page: the way back and the share action, the hero (the
 * cluster's image, or the coverage masthead when it has none or the image fails), then THE PHONE'S
 * WHOLE STORY PAGE as a stack of collapsible sections: Story Intelligence · Breakdown · How each
 * side frames it · Coverage across publishers · Related Topics · Similar Stories. Same six modules,
 * same data, same interactions, all collapsed by default so the page opens as a table of contents.
 *
 * Queries: the story, its intelligence (inside the panel), and ONE ranked similar-stories query.
 */
export default function StoryDetailScreen() {
  const { t, timeAgo, formatDate } = useTranslation();
  const { palette } = useTheme();
  const params = useLocalSearchParams<{ id: string }>();
  const id = params.id ?? "";
  const { data: story, isLoading, isError, error, refetch } = useStory(id);
  const similar = useSimilarStories(id, MAX_CARDS);
  const [heroFailed, setHeroFailed] = React.useState(false);
  const heroSrc = story?.image;
  React.useEffect(() => setHeroFailed(false), [heroSrc]);

  const related = similar.data?.stories ?? [];
  const fmtDate = (iso?: string) => (iso ? formatDate(iso, { month: "short", day: "numeric", year: "numeric" }) : "");

  const backLink = (
    <Pressable accessibilityRole="link" onPress={() => back("/stories")} style={styles.back} hitSlop={6}>
      <Icon name="arrow-left" size={16} color={palette.mutedForeground} />
      <Txt size={14} muted>
        {t("stories.back")}
      </Txt>
    </Pressable>
  );

  if (isLoading) {
    return (
      <Screen>
        <View style={{ marginBottom: 20 }}>{backLink}</View>
        <View style={{ gap: 16 }} accessibilityElementsHidden>
          <Skeleton style={{ aspectRatio: 21 / 9, width: "100%" }} />
          <Skeleton height={160} />
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} height={80} />
          ))}
        </View>
      </Screen>
    );
  }
  if (isError) {
    if ((error as { status?: number } | null)?.status === 404) {
      return (
        <Screen>
          <View style={{ marginBottom: 20 }}>{backLink}</View>
          <EmptyState icon="newspaper" title={t("stories.notFound.title")} description={t("stories.notFound.body")} />
        </Screen>
      );
    }
    return (
      <Screen>
        <View style={{ marginBottom: 20 }}>{backLink}</View>
        <ErrorState onRetry={() => void refetch()} />
      </Screen>
    );
  }
  if (!story) {
    return (
      <Screen>
        <View style={{ marginBottom: 20 }}>{backLink}</View>
        <EmptyState icon="newspaper" title={t("stories.notFound.title")} description={t("stories.notFound.body")} />
      </Screen>
    );
  }

  // MEMBER rows only for every fact this page derives itself (M4 containment).
  const { panel: panelCoverage } = splitCoverage(story.coverage);
  const hasFraming = framingComparison(panelCoverage) !== null;
  const hasTopics = (story.tags ?? []).length > 0;
  const publisherCount = story.publisherCount ?? new Set(panelCoverage.map((c) => c.publisher)).size;
  const showHero = Boolean(story.image) && !heroFailed;

  const share = async () => {
    const url = `${config.apiBaseUrl}/stories/${story.id}`;
    try {
      await Share.share({ title: story.title, message: url, url });
    } catch {
      /* dismissed */
    }
  };

  return (
    <Screen>
      {/* Breadcrumb row: the way back on the left, actions on the right. */}
      <View style={styles.crumbs}>
        {backLink}
        <Button variant="ghost" size="icon" icon="share" accessibilityLabel={t("story.share")} onPress={() => void share()} />
      </View>

      {/* What happened — the hero, with the cluster's real summary as the standfirst. */}
      <Card padded={false}>
        {showHero && (
          <CardImage
            src={story.image}
            aspect={21 / 9}
            radiusPx={0}
            accessibilityLabel={story.title}
            onFallback={() => {
              setHeroFailed(true);
              track("story_hero_error", { host: urlHost(story.image), surface: "detail" });
            }}
          />
        )}
        <View style={{ padding: 20 }}>
          <View style={styles.kicker}>
            {story.topic ? (
              <Txt size={11} weight="600" uppercase tracking={0.6} muted>
                {story.topic}
              </Txt>
            ) : null}
            {story.freshness && <FreshnessBadge band={story.freshness.band} score={story.freshness.score} />}
          </View>

          <Txt display weight="700" size={28} lineHeight={31} tight accessibilityRole="header">
            {story.title}
          </Txt>

          {story.summary ? (
            <Txt size={14} muted lineHeight={22} style={{ marginTop: 10, maxWidth: 672 }}>
              {story.summary}
            </Txt>
          ) : null}

          <View style={styles.dateline}>
            <View style={styles.fact}>
              <Icon name="users" size={14} color={palette.mutedForeground} />
              <Txt size={12} muted>
                {t("stories.publishers", { n: publisherCount })}
              </Txt>
            </View>
            <View style={styles.fact}>
              <Icon name="newspaper" size={14} color={palette.mutedForeground} />
              <Txt size={12} muted>
                {t("stories.articlesCount", { n: story.totalCoverage })}
              </Txt>
            </View>
            {story.earliest ? (
              <Txt size={12} muted>
                {t("stories.firstReport", { date: fmtDate(story.earliest) })}
              </Txt>
            ) : null}
            {story.latest && story.latest !== story.earliest ? (
              <Txt size={12} muted>
                {t("stories.latestReport", { date: fmtDate(story.latest) })}
              </Txt>
            ) : null}
            {story.updatedAt ? (
              <Txt size={12} muted>
                {timeAgo(story.updatedAt)}
              </Txt>
            ) : null}
          </View>

          {showHero && (
            <View style={{ marginTop: 16, maxWidth: 448 }}>
              <SpectrumBar distribution={story.distribution} height={10} />
              {story.blindspotSide && (
                <View style={[styles.thinPill, { borderColor: palette[story.blindspotSide] }]}>
                  <Icon name="eye-off" size={12} color={palette[story.blindspotSide]} />
                  <Txt size={11} weight="500" color={palette[story.blindspotSide]} lineHeight={14}>
                    {t("stories.thinCoverage", { side: t(`filter.${story.blindspotSide}`).toLowerCase() })}
                  </Txt>
                </View>
              )}
            </View>
          )}
        </View>
        {!showHero && <CoveragePlate story={story} />}
      </Card>

      <View style={{ height: 32 }} />

      <StorySections>
        <StorySection title={t("storyIntel.title")} description={t("story.section.intel")}>
          <StoryIntelligencePanel storyId={story.id} />
        </StorySection>

        <StorySection title={t("story.breakdown")} description={t("story.section.breakdown")}>
          <StoryBreakdown story={story} />
        </StorySection>

        {hasFraming && (
          <StorySection title={t("stories.framing.title")} description={t("story.section.framing")}>
            <FramingComparison coverage={panelCoverage} />
          </StorySection>
        )}

        <StorySection title={t("stories.coverageAcross")} description={t("story.section.coverage")}>
          <CoverageList coverage={story.coverage} />
        </StorySection>

        {hasTopics && (
          <StorySection title={t("story.topics")} description={t("story.section.topics")}>
            <StoryTopics story={story} />
          </StorySection>
        )}

        <StorySection title={t("story.related")} description={t("story.section.similar")}>
          <SimilarStories stories={related} isLoading={similar.isLoading} isError={similar.isError} onRetry={() => void similar.refetch()} />
        </StorySection>
      </StorySections>
    </Screen>
  );
}

const styles = StyleSheet.create({
  back: { flexDirection: "row", alignItems: "center", gap: 6 },
  crumbs: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 20 },
  kicker: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 8 },
  dateline: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: 16, rowGap: 4, marginTop: 16 },
  fact: { flexDirection: "row", alignItems: "center", gap: 4 },
  thinPill: { flexDirection: "row", alignItems: "center", gap: 4, alignSelf: "flex-start", marginTop: 8, borderWidth: 1, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 2 },
});
