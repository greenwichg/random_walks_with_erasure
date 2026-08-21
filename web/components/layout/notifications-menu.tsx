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
import { notificationPresentation, notificationHref, badgeLabel } from "@/lib/notifications";
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
      // Payload-aware, because one kind's destination is per-notification: a breaking story opens
      // THAT story, not the stories index. Every other kind resolves to its static href.
      const href = notificationHref(item.kind, item.payload);
      if (href) router.push(href);
    },
    [markSeen, router],
  );

  return (
    // `modal={false}` — a notification list has no business locking the page, and the cost of the
    // default has since been MEASURED: a modal menu throws a scrolled reader to the top of the page
    // and never restores the position, and hides the whole header from assistive tech while open.
    // Full rationale in header.tsx; mechanism in docs/HEADER_MENU_SCROLL.md.
    //
    // (The note that used to sit here called the mechanism unconfirmed, after two headless repros
    // measured zero movement. Those repros were sound — they simply could not show it: the trigger
    // is `html { overflow-x: clip }` in globals.css, which a bare repro page does not have.)
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative text-muted-foreground"
          aria-label={t("header.notifications")}
        >
          {/* No size class: Button's `[&_svg]:size-4` is a descendant selector and outranks one on
              the svg, so `h-[1.15rem]` here rendered at 16px anyway. Verified in a browser. */}
          <Bell />
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

      {/* `collisionPadding` keeps the panel inside the viewport at any scroll position and any
          width — without it a 20rem panel anchored to a right-edge trigger can sit flush against
          (or past) the edge on a narrow phone. `w-[min(20rem,calc(100vw-1rem))]` is the same
          guarantee for the box itself, so the panel narrows rather than overflowing. */}
      <DropdownMenuContent
        align="end"
        collisionPadding={8}
        className="w-[min(20rem,calc(100vw-1rem))] p-0"
      >
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
          // `max-h-96` is 24rem of list on top of the header, the separator and the "show earlier"
          // row — taller than a landscape phone (~375px) and than a short desktop window, where the
          // bottom of the panel then sits off-screen with no way to reach it. Radix publishes the
          // space it actually measured between the trigger and the viewport edge; capping on that
          // (minus ~5rem of chrome) makes the list scroll instead of the panel overflowing, and the
          // 24rem stays as the comfortable ceiling on a tall window.
          <div
            className="overflow-y-auto py-1"
            style={{ maxHeight: "min(24rem, calc(var(--radix-dropdown-menu-content-available-height, 24rem) - 5rem))" }}
          >
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
