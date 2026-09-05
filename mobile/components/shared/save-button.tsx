import * as React from "react";
import { Pressable, StyleSheet, type StyleProp, type ViewStyle } from "react-native";

import type { SavableArticle } from "@ih/core/domain/types";

import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { useSaveArticle, useSaved, useUnsaveArticle } from "@/lib/hooks";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * The single "Save" control. Saved state is the persisted server truth (`useSaved`); a tap
 * toggles it with the same optimistic update and rollback the web runs. `compact` is icon-only for
 * surfaces where the card itself is the primary affordance.
 */
export function SaveButton({
  article,
  compact = false,
  style,
}: {
  article: SavableArticle;
  compact?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const { data: saved } = useSaved();
  const save = useSaveArticle();
  const unsave = useUnsaveArticle();

  const isSaved = (saved ?? []).some((s) => s.articleId === article.id);
  const pending = save.isPending || unsave.isPending;
  const title = isSaved ? t("save.removeTitle") : t("save.saveTitle");
  const fg = isSaved ? palette.primary : palette.mutedForeground;
  const bg = compact ? "transparent" : isSaved ? alpha(palette.primary, 0.15) : palette.muted;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={title}
      accessibilityState={{ selected: isSaved, disabled: pending || !article.id }}
      disabled={pending || !article.id}
      onPress={() => (isSaved ? unsave.mutate(article.id) : save.mutate(article))}
      hitSlop={compact ? 6 : 0}
      style={({ pressed }) => [
        styles.button,
        compact ? styles.compact : styles.labelled,
        { backgroundColor: pressed ? palette.accent : bg, opacity: pending ? 0.7 : 1 },
        style,
      ]}
    >
      <Icon name={isSaved ? "bookmark-check" : "bookmark"} size={14} color={fg} />
      {!compact && (
        <Txt size={12} weight="500" color={fg}>
          {isSaved ? t("save.saved") : t("save.save")}
        </Txt>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, height: 32, borderRadius: radius.lg, alignSelf: "flex-start" },
  labelled: { paddingHorizontal: 12 },
  compact: { width: 32 },
});
