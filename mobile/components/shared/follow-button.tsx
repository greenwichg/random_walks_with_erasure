import * as React from "react";
import { Pressable, StyleSheet, type StyleProp, type ViewStyle } from "react-native";

import type { Settings } from "@ih/core/domain/types";
import { interestForTopic, isFollowedInterest, toggleInterest } from "@ih/core/logic/interests";

import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { useSettings, useUpdateSettings } from "@/lib/hooks";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * The one follow control — "+ / ✓" on a topic chip, "Follow" on a section — over the two contracts
 * Hidden View has: a topic → `Settings.interests` (renders NOTHING for a catalog topic outside the
 * eight sliders, rather than a control that would do nothing), a place → `Settings.locations`.
 * Both write through `useUpdateSettings`, the same mutation the settings screen uses.
 */
export function FollowButton({
  topic,
  place,
  size = "chip",
  color,
  style,
}: {
  topic?: string;
  place?: { placeId: string; level: "country" | "region" | "city" };
  size?: "chip" | "button";
  /** The chip's ink when it sits on an inverted (selected) chip. */
  color?: string;
  style?: StyleProp<ViewStyle>;
}) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const settings = useSettings();
  const update = useUpdateSettings();
  const key = topic ? interestForTopic(topic) : null;

  if (!key && !place) return null;

  const current = settings.data;
  const following = key
    ? isFollowedInterest(current?.interests, key)
    : Boolean(current?.locations?.some((l) => l.placeId === place!.placeId));

  const onToggle = () => {
    if (!current) return;
    const next: Partial<Settings> = key
      ? { interests: toggleInterest(current.interests, key) }
      : {
          locations: following
            ? (current.locations ?? []).filter((l) => l.placeId !== place!.placeId)
            : [...(current.locations ?? []), place!],
        };
    update.mutate(next);
  };

  const label = following ? t("follow.following") : t("follow.follow");
  const disabled = !current || update.isPending;
  const ink = color ?? (size === "chip" ? palette.mutedForeground : palette.foreground);

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ selected: following, disabled }}
      disabled={disabled}
      onPress={onToggle}
      hitSlop={size === "chip" ? 8 : 0}
      style={({ pressed }) => [
        size === "button" && [styles.button, { borderColor: palette.border, backgroundColor: pressed ? palette.accent : "transparent" }],
        disabled && { opacity: 0.5 },
        style,
      ]}
    >
      <Icon name={following ? "check" : "plus"} size={14} color={ink} />
      {size === "button" && (
        <Txt size={13} weight="500" color={ink}>
          {label}
        </Txt>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderRadius: radius.md,
    paddingHorizontal: 12,
    paddingVertical: 6,
    minHeight: 36,
  },
});
