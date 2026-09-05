import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { NotificationItem } from "@ih/core/domain/types";

import { PageTitle, Screen } from "@/components/layout/screen";
import { EmptyState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { useMarkNotificationSeen, useNotifications } from "@/lib/hooks";
import { navigate } from "@/lib/navigation";
import { notificationBodyKey, notificationHref, notificationPresentation } from "@/lib/notifications";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * Alerts — the notification list as a full page. Same rows, same rule as the header panel:
 * `notificationPresentation` is the one place that knows how a KIND renders. Opening a row marks it
 * seen and navigates when its kind has a destination. "Manage" goes to the notification settings.
 */
export default function AlertsScreen() {
  const { t, timeAgo } = useTranslation();
  const { palette } = useTheme();
  const { data, isLoading } = useNotifications();
  const markSeen = useMarkNotificationSeen();
  const items = data ?? [];

  const open = (item: NotificationItem) => {
    if (!item.seenAt) markSeen.mutate(item.id);
    const href = notificationHref(item.kind, item.payload);
    if (href) navigate(href);
  };

  return (
    <Screen pt={16}>
      <PageTitle
        title={t("alerts.title")}
        subtitle={t("alerts.subtitle")}
        action={
          <Button variant="outline" size="sm" icon="sliders" onPress={() => navigate("/settings")}>
            {t("alerts.manage")}
          </Button>
        }
      />

      {isLoading && (
        <View style={{ gap: 12 }} accessibilityElementsHidden>
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} height={64} />
          ))}
        </View>
      )}

      {!isLoading && items.length === 0 && <EmptyState icon="bell" title={t("alerts.empty.title")} description={t("alerts.empty.body")} />}

      {items.length > 0 && (
        <View style={{ borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: palette.border }}>
          {items.map((item) => {
            const pres = notificationPresentation(item.kind);
            const bodyKey = notificationBodyKey(item.kind, item.payload);
            const href = notificationHref(item.kind, item.payload);
            const unseen = !item.seenAt;
            return (
              <Pressable
                key={item.id}
                accessibilityRole="button"
                disabled={!href && !unseen}
                onPress={() => open(item)}
                style={({ pressed }) => [styles.row, { borderBottomColor: palette.border }, pressed && href && { opacity: 0.7 }]}
              >
                <View style={{ marginTop: 2 }}>
                  <Icon name={pres.icon} size={16} color={palette.mutedForeground} />
                  {unseen && <View style={[styles.dot, { backgroundColor: palette.primary }]} />}
                </View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Txt size={15} weight={unseen ? "600" : "500"} muted={!unseen} lineHeight={19} tight>
                    {t(pres.titleKey)}
                  </Txt>
                  {bodyKey && (
                    <Txt size={13} muted lineHeight={20} style={{ marginTop: 4 }}>
                      {t(bodyKey, item.payload as Record<string, unknown>)}
                    </Txt>
                  )}
                  <Txt size={11} muted style={{ marginTop: 4, opacity: 0.8 }}>
                    {timeAgo(item.createdAt)}
                  </Txt>
                </View>
              </Pressable>
            );
          })}
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingVertical: 16, borderBottomWidth: StyleSheet.hairlineWidth },
  dot: { position: "absolute", left: -6, top: -4, width: 6, height: 6, borderRadius: radius.pill },
});
