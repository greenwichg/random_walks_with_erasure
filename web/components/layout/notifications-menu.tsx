"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Bell } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useNotifications, useMarkNotificationSeen } from "@/hooks/use-data";
import { notificationPresentation, badgeLabel } from "@/lib/notifications";
import { useTranslation } from "@/lib/i18n";
import { timeAgo } from "@/lib/i18n-core";
import { cn } from "@/lib/utils";
import type { NotificationItem } from "@/types/domain";

/**
 * Header bell + unread badge + a dropdown panel of the reader's notifications (N3). Data comes from
 * `useNotifications` (fetched once, cached); the unread count and unread emphasis are DERIVED from
 * that list. Kind → icon / title / body / destination is resolved ONLY through
 * `notificationPresentation`, so an unknown kind degrades to a safe generic row instead of crashing.
 * Clicking a row marks it seen (cache updated in place) and navigates when the kind has a destination.
 */
export function NotificationsMenu() {
  const { t, lang } = useTranslation();
  const router = useRouter();
  const { data, isLoading } = useNotifications();
  const markSeen = useMarkNotificationSeen();

  const items: NotificationItem[] = data ?? [];
  const unread = items.reduce((n, x) => (x.seenAt ? n : n + 1), 0);
  const badge = badgeLabel(unread);

  const onSelect = React.useCallback(
    (item: NotificationItem) => {
      if (!item.seenAt) markSeen.mutate(item.id);
      const { href } = notificationPresentation(item.kind);
      if (href) router.push(href);
    },
    [markSeen, router],
  );

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative text-muted-foreground"
          aria-label={t("header.notifications")}
        >
          <Bell className="h-[1.15rem] w-[1.15rem]" />
          {badge ? (
            <span
              aria-hidden
              className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-primary px-1 text-[0.6rem] font-semibold leading-none text-primary-foreground"
            >
              {badge}
            </span>
          ) : null}
          {unread > 0 ? (
            <span className="sr-only">{t("notifications.unreadCount", { count: unread })}</span>
          ) : null}
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-80 p-0">
        <DropdownMenuLabel className="px-3 py-2 text-sm font-semibold">
          {t("notifications.title")}
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="my-0" />

        {isLoading ? (
          <div className="space-y-2 p-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="px-3 py-10 text-center text-sm text-muted-foreground">
            {t("notifications.empty")}
          </div>
        ) : (
          <div className="max-h-96 overflow-y-auto py-1">
            {items.map((item) => {
              const pres = notificationPresentation(item.kind);
              const Icon = pres.icon;
              const title = t(pres.titleKey);
              const body = pres.bodyKey ? t(pres.bodyKey, item.payload) : null;
              const when = timeAgo(item.createdAt, lang, t);
              const unseen = !item.seenAt;
              return (
                <DropdownMenuItem
                  key={item.id}
                  onSelect={() => onSelect(item)}
                  className="flex cursor-pointer items-start gap-3 px-3 py-2.5"
                >
                  <span className="relative mt-0.5 shrink-0">
                    <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
                    {unseen ? (
                      <span
                        aria-hidden
                        className="absolute -left-1.5 -top-1 h-1.5 w-1.5 rounded-full bg-primary"
                      />
                    ) : null}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span
                      className={cn(
                        "block text-sm leading-snug",
                        unseen ? "font-medium text-foreground" : "text-muted-foreground",
                      )}
                    >
                      {title}
                    </span>
                    {body ? (
                      <span className="mt-0.5 block text-xs text-muted-foreground">{body}</span>
                    ) : null}
                    {when ? (
                      <span className="mt-0.5 block text-[0.7rem] text-muted-foreground/80">{when}</span>
                    ) : null}
                  </span>
                </DropdownMenuItem>
              );
            })}
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
