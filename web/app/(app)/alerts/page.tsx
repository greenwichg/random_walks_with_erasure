"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Bell, Settings2 } from "lucide-react";
import type { NotificationItem } from "@ih/core/domain/types";
import { useMarkNotificationSeen, useNotifications } from "@/hooks/use-data";
import { notificationBodyKey, notificationHref, notificationPresentation } from "@/lib/notifications";
import { PageContainer } from "@/components/layout/page-container";
import { AccountTabs } from "@/components/shared/account-tabs";
import { EmptyState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Alerts — the reference's alert list, as a full page rather than only the header panel's
 * dropdown. Same rows, same rule: `notificationPresentation` is the one place that knows how a
 * KIND renders, so this page and the panel can never describe the same notification differently,
 * and an unknown kind degrades to a generic row here exactly as it does there.
 *
 * Opening a row marks it seen and navigates when its kind has a destination — the same flow the
 * panel runs. There is no Delete: the engine exposes marking a notification seen and nothing
 * else, so a delete button would be a control with no contract behind it. "Manage" goes to the
 * notification settings, which is where the reader turns kinds and channels on and off.
 */
export default function AlertsPage() {
  const { t, timeAgo } = useTranslation();
  const router = useRouter();
  const { data, isLoading } = useNotifications();
  const markSeen = useMarkNotificationSeen();
  const items = data ?? [];

  const open = (item: NotificationItem) => {
    if (!item.seenAt) markSeen.mutate(item.id);
    const href = notificationHref(item.kind, item.payload);
    if (href) router.push(href);
  };

  return (
    <PageContainer className="pt-4">
      <AccountTabs />

      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("alerts.title")}</h1>
          <p className="mt-1 max-w-xl text-sm text-muted-foreground">{t("alerts.subtitle")}</p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/settings">
            <Settings2 className="h-3.5 w-3.5" aria-hidden />
            {t("alerts.manage")}
          </Link>
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-16 rounded-md" />
          ))}
        </div>
      )}

      {!isLoading && items.length === 0 && (
        <EmptyState icon={Bell} title={t("alerts.empty.title")} description={t("alerts.empty.body")} />
      )}

      {items.length > 0 && (
        <ul className="border-t">
          {items.map((item) => {
            const pres = notificationPresentation(item.kind);
            const Icon = pres.icon;
            const bodyKey = notificationBodyKey(item.kind, item.payload);
            const href = notificationHref(item.kind, item.payload);
            const unseen = !item.seenAt;
            return (
              <li key={item.id} className="border-b">
                <button
                  type="button"
                  onClick={() => open(item)}
                  disabled={!href && !unseen}
                  className={cn(
                    "flex w-full items-start gap-3 py-4 text-left transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                    href ? "hover:text-primary" : "cursor-default",
                  )}
                >
                  <span className="relative mt-0.5 shrink-0">
                    <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
                    {unseen && (
                      <span
                        aria-hidden
                        className="absolute -left-1.5 -top-1 h-1.5 w-1.5 rounded-full bg-primary"
                      />
                    )}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span
                      className={cn(
                        "block text-[15px] font-semibold leading-snug tracking-tight",
                        !unseen && "font-medium text-muted-foreground",
                      )}
                    >
                      {t(pres.titleKey)}
                    </span>
                    {bodyKey && (
                      <span className="mt-1 block text-[13px] leading-relaxed text-muted-foreground">
                        {t(bodyKey, item.payload as Record<string, unknown>)}
                      </span>
                    )}
                    <span className="mt-1 block text-[11px] text-muted-foreground/80">
                      {timeAgo(item.createdAt)}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </PageContainer>
  );
}
