import * as React from "react";
import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import { Button } from "@/components/ui/button";
import { Icon, type IconName } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/** Reusable empty state: a dashed panel with an icon disc, a title and a line. */
export function EmptyState({
  icon = "inbox",
  title,
  description,
  action,
  style,
}: {
  icon?: IconName;
  title?: string;
  description?: string;
  action?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  return (
    <View style={[styles.panel, { borderColor: palette.border, backgroundColor: alpha(palette.card, 0.4), borderStyle: "dashed" }, style]}>
      <View style={[styles.disc, { backgroundColor: palette.muted }]}>
        <Icon name={icon} size={24} color={palette.mutedForeground} />
      </View>
      <Txt weight="500" align="center">
        {title ?? t("states.empty.title")}
      </Txt>
      {description && (
        <Txt size={14} muted align="center" style={{ marginTop: 4, maxWidth: 384 }}>
          {description}
        </Txt>
      )}
      {action && <View style={{ marginTop: 20 }}>{action}</View>}
    </View>
  );
}

/** In-card placeholder for a chart with nothing to plot — quieter than EmptyState on purpose. */
export function ChartEmpty({ height, style }: { height?: number; style?: StyleProp<ViewStyle> }) {
  const { t } = useTranslation();
  return (
    <View style={[{ minHeight: height, alignItems: "center", justifyContent: "center" }, style]}>
      <Txt size={14} muted>
        {t("states.chartEmpty")}
      </Txt>
    </View>
  );
}

/** Reusable error state with a retry action. */
export function ErrorState({
  message,
  onRetry,
  style,
}: {
  message?: string;
  onRetry?: () => void;
  style?: StyleProp<ViewStyle>;
}) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  return (
    <View style={[styles.panel, { borderColor: alpha(palette.destructive, 0.2), backgroundColor: alpha(palette.destructive, 0.03) }, style]}>
      <View style={[styles.disc, { backgroundColor: alpha(palette.destructive, 0.1) }]}>
        <Icon name="alert-circle" size={24} color={palette.destructive} />
      </View>
      <Txt weight="500" align="center">
        {t("states.error.title")}
      </Txt>
      <Txt size={14} muted align="center" style={{ marginTop: 4, maxWidth: 384 }}>
        {message ?? t("states.error.body")}
      </Txt>
      {onRetry && (
        <Button variant="outline" size="sm" icon="refresh" onPress={onRetry} style={{ marginTop: 20 }}>
          {t("common.tryAgain")}
        </Button>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  panel: {
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderRadius: radius.lg,
    paddingHorizontal: 24,
    paddingVertical: 64,
  },
  disc: { width: 48, height: 48, borderRadius: 16, alignItems: "center", justifyContent: "center", marginBottom: 16 },
});
