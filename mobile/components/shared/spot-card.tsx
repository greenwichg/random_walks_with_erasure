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
 * A picture card for a single story — the reference's blind-spot and topic cards. Picture (or
 * the shared fallback) → labelled coverage strip → kicker → 13px headline.
 */
export function SpotCard({
  story,
  showTopic = true,
  style,
}: {
  story: Story;
  showTopic?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const { timeAgo } = useTranslation();
  const kicker = [showTopic ? story.topic : "", story.updatedAt ? timeAgo(story.updatedAt) : ""]
    .filter(Boolean)
    .join(" · ");

  return (
    <Pressable
      accessibilityRole="link"
      onPress={() => navigate(`/stories/${story.id}`)}
      style={({ pressed }) => [style, pressed && styles.pressed]}
    >
      <CardImage src={story.image} aspect={16 / 9} radiusPx={radius.md} />
      <View style={{ marginTop: 8 }}>
        <BiasStrip distribution={story.distribution} labels />
      </View>
      {kicker ? (
        <Txt size={11} muted style={{ marginTop: 6 }}>
          {kicker}
        </Txt>
      ) : null}
      <Txt display weight="600" size={13} lineHeight={17} tight style={{ marginTop: 4 }}>
        {story.title}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({ pressed: { opacity: 0.85 } });
