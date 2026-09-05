import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { NotificationItem } from "@ih/core/domain/types";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Icon } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { Txt } from "@/components/ui/text";
import { radius, space } from "@/design/tokens";
import { useMarkNotificationSeen, useNotifications } from "@/lib/hooks";
import { navigate } from "@/lib/navigation";
import { badgeLabel, notificationBodyKey, notificationHref, notificationPresentation } from "@/lib/notifications";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * Header bell + unread badge + the panel of the reader's notifications. The unread count and the
 * unread emphasis are DERIVED from the cached list; a tap marks the row seen (cache updated in
 * place) and navigates when the kind has a destination. Active (unseen) rows lead; settled history
 * stays behind "show earlier". Kind → icon/title/body/destination is resolved only through
 * `notificationPresentation`, so an unknown kind degrades to a safe generic row.
 */
export function NotificationsMenu() {
  const { t, timeAgo } = useTranslation();
  const { palette } = useTheme();
  const { data, isLoading } = useNotifications();
  const markSeen = useMarkNotificationSeen();
  const [open, setOpen] = React.useState(false);
  const [showEarlier, setShowEarlier] = React.useState(false);

  const all = React.useMemo<NotificationItem[]>(() => data ?? [], [data]);
  const active = React.useMemo(() => all.filter((x) => !x.seenAt), [all]);
  const earlier = React.useMemo(() => all.filter((x) => x.seenAt), [all]);
  const items = showEarlier ? [...active, ...earlier] : active;
  const unread = active.length;
  const badge = badgeLabel(unread);

  const onSelect = (item: NotificationItem) => {
    if (!item.seenAt) markSeen.mutate(item.id);
    const href = notificationHref(item.kind, item.payload);
    setOpen(false);
    if (href) navigate(href);
  };

  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={
          unread > 0 ? `${t("header.notifications")}. ${t("notifications.unreadCount", { count: unread })}` : t("header.notifications")
        }
        onPress={() => setOpen(true)}
        style={({ pressed }) => [styles.bell, pressed && { backgroundColor: palette.accent }]}
      >
        <Icon name="bell" size={18} color={palette.mutedForeground} />
        {badge ? (
          <View style={[styles.badge, { backgroundColor: palette.primary }]}>
            <Txt size={10} weight="600" color={palette.primaryForeground} lineHeight={12}>
              {badge}
            </Txt>
          </View>
        ) : null}
      </Pressable>

      <BottomSheet open={open} onClose={() => setOpen(false)} title={t("notifications.title")}>
        {isLoading ? (
          <View style={{ gap: space.sm, padding: space.md }}>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} height={48} />
            ))}
          </View>
        ) : items.length === 0 ? (
          <Txt size={14} muted align="center" style={{ paddingVertical: 40, paddingHorizontal: 12 }}>
            {t("notifications.empty")}
          </Txt>
        ) : (
          <View style={{ paddingVertical: 4 }}>
            {items.map((item) => {
              const pres = notificationPresentation(item.kind);
              const bodyKey = notificationBodyKey(item.kind, item.payload);
              const body = bodyKey ? t(bodyKey, item.payload) : null;
              const when = timeAgo(item.createdAt);
              const unseen = !item.seenAt;
              return (
                <Pressable
                  key={item.id}
                  accessibilityRole="menuitem"
                  onPress={() => onSelect(item)}
                  style={({ pressed }) => [styles.row, pressed && { backgroundColor: palette.accent }]}
                >
                  <View style={styles.iconWrap}>
                    <Icon name={pres.icon} size={16} color={palette.mutedForeground} />
                    {unseen ? <View style={[styles.dot, { backgroundColor: palette.primary }]} /> : null}
                  </View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Txt size={14} weight={unseen ? "500" : "400"} muted={!unseen} lineHeight={18}>
                      {t(pres.titleKey)}
                    </Txt>
                    {body ? (
                      <Txt size={12} muted style={{ marginTop: 2 }}>
                        {body}
                      </Txt>
                    ) : null}
                    {when ? (
                      <Txt size={11} muted style={{ marginTop: 2, opacity: 0.8 }}>
                        {when}
                      </Txt>
                    ) : null}
                  </View>
                </Pressable>
              );
            })}
          </View>
        )}

        {!isLoading && earlier.length > 0 && (
          <Pressable
            accessibilityRole="button"
            onPress={() => setShowEarlier((v) => !v)}
            style={[styles.earlier, { borderTopColor: palette.border }]}
          >
            <Txt size={12} muted>
              {showEarlier ? t("notifications.hideEarlier") : t("notifications.showEarlier", { count: earlier.length })}
            </Txt>
          </Pressable>
        )}
      </BottomSheet>
    </>
  );
}

const styles = StyleSheet.create({
  bell: { width: 36, height: 36, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  badge: {
    position: "absolute",
    top: 2,
    right: 2,
    minWidth: 16,
    height: 16,
    paddingHorizontal: 4,
    borderRadius: radius.pill,
    alignItems: "center",
    justifyContent: "center",
  },
  row: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingHorizontal: 12, paddingVertical: 10, borderRadius: radius.sm },
  iconWrap: { marginTop: 2 },
  dot: { position: "absolute", left: -6, top: -4, width: 6, height: 6, borderRadius: radius.pill },
  earlier: { borderTopWidth: StyleSheet.hairlineWidth, paddingHorizontal: 12, paddingVertical: 10, marginTop: 4 },
});
