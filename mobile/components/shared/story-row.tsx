import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { Story } from "@ih/core/domain/types";

import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { BiasStrip } from "./bias-strip";
import { CardImage } from "./card-image";

/**
 * ONE story row, shared by every list:
 *
 *   kicker (topic · age) → headline → coverage strip + "N% Centre coverage: N sources"
 *   → optional "See the story" affordance, with an optional square thumbnail on the right.
 *
 * `lg` is the mobile feed (17px headline, 88px thumb), `md` a topic section's rows (15px, 72px),
 * `sm` closing runs. Rows are separated by hairlines, never cards.
 */
export function StoryRow({
  story,
  size = "sm",
  thumb = false,
  showTopic = true,
  action = false,
  last = false,
}: {
  story: Story;
  size?: "sm" | "md" | "lg";
  thumb?: boolean;
  showTopic?: boolean;
  action?: boolean;
  /** `last:border-b-0`. */
  last?: boolean;
}) {
  const { t, timeAgo } = useTranslation();
  const { palette } = useTheme();
  const kicker = [showTopic ? story.topic : "", story.updatedAt ? timeAgo(story.updatedAt) : ""]
    .filter(Boolean)
    .join(" · ");
  const py = size === "lg" ? 16 : size === "md" ? 14 : 12;

  return (
    <Pressable
      accessibilityRole="link"
      onPress={() => navigate(`/stories/${story.id}`)}
      style={({ pressed }) => [
        styles.row,
        { paddingVertical: py, borderBottomWidth: last ? 0 : StyleSheet.hairlineWidth, borderBottomColor: alpha(palette.border, 0.7) },
        pressed && styles.pressed,
      ]}
    >
      <View style={styles.text}>
        {kicker ? (
          <Txt size={11} muted lineHeight={13} style={{ marginBottom: 4 }}>
            {kicker}
          </Txt>
        ) : null}
        <Txt
          display
          weight="600"
          size={size === "lg" ? 17 : size === "md" ? 15 : 14}
          lineHeight={size === "lg" ? 22 : size === "md" ? 20 : 19}
          tight
        >
          {story.title}
        </Txt>
        <BiasStrip
          distribution={story.distribution}
          sources={story.totalCoverage}
          style={[{ marginTop: 8 }, size !== "lg" && { maxWidth: 352 }]}
        />
        {action && (
          <Txt size={13} weight="500" style={styles.action}>
            {t("storyCard.seeStory")}
          </Txt>
        )}
      </View>
      {thumb && (
        <CardImage
          src={story.image}
          aspect={1}
          radiusPx={radius.md}
          style={{ width: size === "lg" ? 88 : 72, flexShrink: 0 }}
        />
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: 12 },
  text: { flex: 1, minWidth: 0 },
  action: { marginTop: 8, textDecorationLine: "underline" },
  pressed: { opacity: 0.85 },
});
