import * as React from "react";
import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import type { Story } from "@ih/core/domain/types";

import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { navigate } from "@/lib/navigation";
import { useTranslation } from "@/lib/i18n-context";

import { BiasStrip } from "./bias-strip";
import { CardImage } from "./card-image";

/**
 * The lead — one story at display scale: picture (or the shared newspaper fallback), the labelled
 * coverage strip, the headline, and a dateline of counted facts. `lg` for a page lead (26px),
 * `md` inside a topic section (21px) — the mobile web's two sizes exactly.
 */
export function LeadStory({
  story,
  size = "lg",
  style,
}: {
  story: Story;
  size?: "md" | "lg";
  style?: StyleProp<ViewStyle>;
}) {
  const { t, formatCompact } = useTranslation();
  const publisherCount = story.publisherCount ?? story.publishers?.length ?? null;

  return (
    <Pressable
      accessibilityRole="link"
      onPress={() => navigate(`/stories/${story.id}`)}
      style={({ pressed }) => [style, pressed && styles.pressed]}
    >
      <CardImage src={story.image} aspect={16 / 9} radiusPx={radius.md} accessibilityLabel={story.title} />
      <View style={{ marginTop: 12 }}>
        <BiasStrip distribution={story.distribution} labels />
      </View>
      <Txt
        display
        weight="700"
        size={size === "lg" ? 26 : 21}
        lineHeight={size === "lg" ? 30 : 24}
        tight
        style={{ marginTop: 12 }}
        accessibilityRole="header"
      >
        {story.title}
      </Txt>
      <Txt size={12} muted style={{ marginTop: 8 }}>
        {[
          story.topic,
          t("storyCard.sources", { n: formatCompact(story.totalCoverage) }),
          publisherCount != null ? t("stories.publishers", { n: formatCompact(publisherCount) }) : "",
        ]
          .filter(Boolean)
          .join(" · ")}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({ pressed: { opacity: 0.85 } });
