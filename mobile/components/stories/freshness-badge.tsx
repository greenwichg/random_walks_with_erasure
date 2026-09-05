import * as React from "react";
import { StyleSheet, View } from "react-native";

import type { FreshnessBand } from "@ih/core/domain/types";

import { Icon, type IconName } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { tw } from "@/design/tailwind";
import { alpha, radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * A compact freshness badge for a Story — colour + icon encode the band (Breaking → Archived).
 * Semantic colours only (Tailwind's red / amber / emerald / sky, as on the web), never the accent.
 */
export function FreshnessBadge({ band, score, showScore = false }: { band: FreshnessBand; score?: number; showScore?: boolean }) {
  const { t } = useTranslation();
  const { palette, scheme } = useTheme();
  const dark = scheme === "dark";

  const meta: Record<FreshnessBand, { icon: IconName; bg: string; fg: string; ring: string }> = {
    Breaking: { icon: "flame", bg: alpha(tw.red500, 0.12), fg: dark ? tw.red400 : tw.red600, ring: alpha(tw.red500, 0.2) },
    Developing: { icon: "trending-up", bg: alpha(tw.amber500, 0.12), fg: dark ? tw.amber400 : tw.amber600, ring: alpha(tw.amber500, 0.2) },
    Active: { icon: "radio", bg: alpha(tw.emerald500, 0.12), fg: dark ? tw.emerald400 : tw.emerald600, ring: alpha(tw.emerald500, 0.2) },
    Cooling: { icon: "snowflake", bg: alpha(tw.sky500, 0.12), fg: dark ? tw.sky400 : tw.sky600, ring: alpha(tw.sky500, 0.2) },
    Archived: { icon: "archive", bg: palette.muted, fg: palette.mutedForeground, ring: palette.border },
  };
  const m = meta[band];
  if (!m) return null;
  const bandLabel = t(`freshness.${band}`);

  return (
    <View
      accessible
      accessibilityLabel={typeof score === "number" ? t("freshness.title", { band: bandLabel, score }) : bandLabel}
      style={[styles.pill, { backgroundColor: m.bg, borderColor: m.ring }]}
    >
      <Icon name={m.icon} size={14} color={m.fg} />
      <Txt size={12} weight="500" color={m.fg} lineHeight={16}>
        {bandLabel}
        {showScore && typeof score === "number" ? <Txt size={12} color={m.fg} tabular style={{ opacity: 0.7 }}>{` · ${score}`}</Txt> : null}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderRadius: radius.pill,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 2,
    alignSelf: "flex-start",
  },
});
