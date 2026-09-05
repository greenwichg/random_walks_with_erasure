import * as React from "react";
import { Pressable, StyleSheet, type StyleProp, type ViewStyle } from "react-native";

import type { Article } from "@ih/core/domain/types";

import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { useReadArticleAction } from "@/lib/read-article";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

export { useReadArticleAction };

type ReadableArticle = Pick<Article, "url"> & Partial<Pick<Article, "id" | "headline" | "description">>;

/**
 * The single "Read article" control, shared by every surface so the Read flow behaves identically
 * everywhere — a thin shell over `useReadArticleAction`. `soft` is the quiet tinted treatment for
 * dense list rows. Opens only an absolute http(s) URL; with none it records `onOpen` or disables.
 */
export function ReadArticleButton({
  article,
  openedFrom,
  onOpen,
  label,
  variant = "solid",
  compact = false,
  style,
}: {
  article: ReadableArticle;
  openedFrom?: string;
  onOpen?: () => void;
  label?: string;
  variant?: "solid" | "soft";
  /** `h-7 px-2.5` — the coverage row's smaller pill. */
  compact?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const { opened, href, actionable, open } = useReadArticleAction(article, openedFrom, onOpen);

  const colors = opened
    ? { bg: alpha(palette.positive, 0.15), fg: palette.positive }
    : actionable
      ? variant === "soft"
        ? { bg: alpha(palette.primary, 0.1), fg: palette.primary }
        : { bg: palette.primary, fg: palette.primaryForeground }
      : { bg: palette.muted, fg: palette.mutedForeground };

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !actionable, selected: opened }}
      accessibilityLabel={href ? t("read.openTitle") : onOpen ? t("read.recordTitle") : t("read.noLinkTitle")}
      disabled={!actionable}
      onPress={open}
      style={({ pressed }) => [
        styles.button,
        compact && styles.compact,
        { backgroundColor: colors.bg, opacity: pressed ? 0.85 : 1 },
        style,
      ]}
    >
      <Icon name={opened ? "check" : href ? "external-link" : "book-open"} size={14} color={colors.fg} />
      <Txt size={12} weight="500" color={colors.fg} numberOfLines={1}>
        {opened ? t("read.opened") : href ? (label ?? t("read.readArticle")) : t("read.noLink")}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    height: 32,
    paddingHorizontal: 12,
    borderRadius: radius.lg,
    alignSelf: "flex-start",
  },
  compact: { height: 28, paddingHorizontal: 10 },
});
