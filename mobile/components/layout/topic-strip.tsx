import * as React from "react";
import { Pressable, ScrollView, StyleSheet, View } from "react-native";

import { FollowButton } from "@/components/shared/follow-button";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { useDiscover } from "@/lib/hooks";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/** How many catalog topics the strip shows — the reference's row. */
const TOPIC_LIMIT = 12;

/**
 * The topic chip strip under the masthead — chrome, not page content: the reference carries it on
 * every screen. A chip does two things, which is why the follow control is INSIDE it: the label
 * goes to that topic's stories, and the `+`/`✓` follows the interest behind it (absent for a topic
 * with no interest slider, so the strip never offers a control that would write nothing).
 *
 * Topics come from the catalog facets the filters and the menu already fetch — a cached query.
 */
export function TopicStrip() {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const facets = useDiscover({});
  const topics = (facets.data?.topics ?? []).slice(0, TOPIC_LIMIT);
  if (topics.length === 0) return null;

  return (
    <View style={[styles.wrap, { backgroundColor: palette.card, borderBottomColor: palette.border }]}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.row}
        accessibilityLabel={t("home.trending.title")}
      >
        {topics.map((topic) => (
          <View key={topic} style={[styles.chip, { borderColor: palette.border, backgroundColor: palette.card }]}>
            <Pressable accessibilityRole="link" onPress={() => navigate(`/stories?topic=${encodeURIComponent(topic)}`)} hitSlop={6}>
              <Txt size={12} weight="500" lineHeight={16} style={{ opacity: 0.8 }}>
                {topic}
              </Txt>
            </Pressable>
            <FollowButton topic={topic} />
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { borderBottomWidth: StyleSheet.hairlineWidth },
  row: { height: 44, alignItems: "center", gap: 8, paddingHorizontal: 16 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: radius.pill,
    paddingLeft: 12,
    paddingRight: 8,
    paddingVertical: 4,
  },
});
