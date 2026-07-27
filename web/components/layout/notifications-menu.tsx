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

  // ACTIVE (unseen) vs SETTLED (seen) — the panel leads with what still needs attention and keeps
  // history behind a toggle, so a long-lived account's inbox isn't dominated by rows describing
  // states that have already been handled or auto-resolved by the engine.
  const all = React.useMemo<NotificationItem[]>(() => data ?? [], [data]);
  const active = React.useMemo(() => all.filter((x) => !x.seenAt), [all]);
  const earlier = React.useMemo(() => all.filter((x) => x.seenAt), [all]);
  const [showEarlier, setShowEarlier] = React.useState(false);
  const items = showEarlier ? [...active, ...earlier] : active;
  const unread = active.length;
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

        {/* Settled history stays reachable but never crowds the actionable list. */}
        {!isLoading && earlier.length > 0 && (
          <>
            <DropdownMenuSeparator className="my-0" />
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                setShowEarlier((v) => !v);
              }}
              className="w-full px-3 py-2 text-left text-xs text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
            >
              {showEarlier
                ? t("notifications.hideEarlier")
                : t("notifications.showEarlier", { count: earlier.length })}
            </button>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
