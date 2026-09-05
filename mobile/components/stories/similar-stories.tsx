import * as React from "react";
import { Pressable, ScrollView, Share, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type { Story } from "@ih/core/domain/types";
import { interestForTopic, isFollowedInterest, toggleInterest } from "@ih/core/logic/interests";

import { CardImage } from "@/components/shared/card-image";
import { BottomSheet, SheetItem } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { config } from "@/lib/config";
import { useSettings, useUpdateSettings } from "@/lib/hooks";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/** How many cards the rail shows. */
export const MAX_CARDS = 10;
const CARD_W = 300;

/**
 * SIMILAR STORIES — the story page's "what else is this like" rail. What it renders is decided
 * upstream (`/api/stories/{id}/similar`); it holds no notion of "similar" and never pads. Three
 * outcomes, three renders: in flight → a skeleton row; failed → a retry notice; empty → one line
 * saying nothing else covers this event. Edge-to-edge: the first card sits on the page gutter and
 * the last scrolls past it, so the next card PEEKS. Inside the collapsible panel this is the body.
 */
export function SimilarStories({
  stories,
  isLoading = false,
  isError = false,
  onRetry,
}: {
  stories: Story[];
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
}) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const gutter = Math.max(16, insets.left);
  const shown = stories.slice(0, MAX_CARDS);

  if (isLoading) {
    return (
      <View style={[styles.rail, { paddingBottom: 16 }]} accessibilityElementsHidden>
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} height={163} width={CARD_W} />
        ))}
      </View>
    );
  }
  if (isError) {
    return (
      <View style={styles.notice}>
        <Txt size={14} muted>
          {t("story.similar.error")}
        </Txt>
        {onRetry && (
          <Button variant="outline" size="sm" icon="refresh" onPress={onRetry}>
            {t("common.tryAgain")}
          </Button>
        )}
      </View>
    );
  }
  if (shown.length === 0) {
    return (
      <View style={styles.notice}>
        <Txt size={14} muted>
          {t("story.similar.none")}
        </Txt>
      </View>
    );
  }

  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      snapToInterval={CARD_W + 12}
      snapToAlignment="start"
      decelerationRate="fast"
      style={{ marginHorizontal: -gutter }}
      contentContainerStyle={[styles.rail, { paddingHorizontal: gutter, paddingBottom: 16 }]}
    >
      {shown.map((story) => (
        <SimilarStoryCard key={story.id} story={story} />
      ))}
    </ScrollView>
  );
}

/** One card: a square mark on the left, the headline and its dateline, a footer with the counted total and the menu. */
function SimilarStoryCard({ story }: { story: Story }) {
  const { t, formatCompact, timeAgo } = useTranslation();
  const { palette } = useTheme();
  return (
    <Card padded={false} style={{ width: CARD_W }}>
      <Pressable accessibilityRole="link" onPress={() => navigate(`/stories/${story.id}`)} style={({ pressed }) => [styles.cardBody, pressed && { opacity: 0.85 }]}>
        <CardImage src={story.image} aspect={1} radiusPx={0} style={{ width: 112, flexShrink: 0 }} />
        <View style={styles.cardText}>
          <Txt display weight="700" size={15} lineHeight={19} tight numberOfLines={4}>
            {story.title}
          </Txt>
          <Txt size={12} muted style={{ marginTop: "auto", paddingTop: 8 }}>
            {timeAgo(story.updatedAt)}
          </Txt>
        </View>
      </Pressable>
      <View style={[styles.cardFooter, { borderTopColor: palette.border }]}>
        <Txt size={13} weight="600">
          {t("storyCard.sources", { n: formatCompact(story.totalCoverage) })}
        </Txt>
        <StoryCardMenu story={story} />
      </View>
    </Card>
  );
}

/** The card's overflow menu — only what the product can actually DO to a story from here: share
 *  its URL (the platform sheet), and follow its topic when it maps to an interest slider. */
function StoryCardMenu({ story }: { story: Story }) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const settings = useSettings();
  const update = useUpdateSettings();
  const [open, setOpen] = React.useState(false);

  const key = story.topic ? interestForTopic(story.topic) : null;
  const following = key ? isFollowedInterest(settings.data?.interests, key) : false;

  const share = async () => {
    setOpen(false);
    const url = `${config.apiBaseUrl}/stories/${story.id}`;
    try {
      await Share.share({ title: story.title, message: url, url });
    } catch {
      /* sheet dismissed — nothing to report */
    }
  };

  const onFollow = () => {
    setOpen(false);
    const current = settings.data;
    if (!current || !key) return;
    update.mutate({ interests: toggleInterest(current.interests, key) });
  };

  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t("story.similar.options")}
        onPress={() => setOpen(true)}
        hitSlop={6}
        style={({ pressed }) => [styles.menuButton, pressed && { backgroundColor: palette.accent }]}
      >
        <Icon name="more-vertical" size={16} color={palette.mutedForeground} />
      </Pressable>
      <BottomSheet open={open} onClose={() => setOpen(false)}>
        <SheetItem label={t("story.share")} icon="share" onPress={() => void share()} />
        {key && story.topic && (
          <SheetItem
            label={t(following ? "story.similar.unfollowTopic" : "story.similar.followTopic", { topic: story.topic })}
            icon={following ? "check" : "plus"}
            onPress={onFollow}
          />
        )}
      </BottomSheet>
    </>
  );
}

const styles = StyleSheet.create({
  rail: { flexDirection: "row", gap: 12 },
  notice: { alignItems: "flex-start", gap: 12, paddingBottom: 24 },
  cardBody: { flexDirection: "row" },
  cardText: { flex: 1, minWidth: 0, padding: 12 },
  cardFooter: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, borderTopWidth: StyleSheet.hairlineWidth, paddingHorizontal: 12, paddingVertical: 8 },
  menuButton: { width: 32, height: 32, borderRadius: radius.pill, alignItems: "center", justifyContent: "center", marginRight: -4 },
});
