import { bodyKeyFor, hrefFor, kindMeta } from "@ih/core/logic/notification-kinds";

import type { IconName } from "@/components/ui/icon";

export { badgeLabel } from "@ih/core/logic/notification-kinds";

/**
 * Notification presentation — kind → icon / title key / body key / destination. The metadata
 * (keys, destinations, deep-link freshness) is the shared table in `@ih/core`; the icon is the one
 * per-kind fact that belongs to a renderer, so it lives beside the icons, as on the web.
 */
const ICONS: Record<string, IconName> = {
  weekly_report: "activity",
  monthly_deep_dive: "calendar",
  recommendations_waiting: "sparkles",
  weekly_digest: "bar-chart",
  streak_reminder: "flame",
  blind_spot_alert: "eye",
  breaking_story: "zap",
};

export interface NotificationPresentation {
  icon: IconName;
  titleKey: string;
  bodyKey: string | null;
  href: string | null;
}

export function notificationHref(kind: string, payload?: unknown): string | null {
  return hrefFor(kind, payload);
}

export function notificationBodyKey(kind: string, payload?: unknown): string | null {
  return bodyKeyFor(kindMeta(kind), payload);
}

export function notificationPresentation(kind: string): NotificationPresentation {
  const meta = kindMeta(kind);
  return { icon: ICONS[kind] ?? "bell", titleKey: meta.titleKey, bodyKey: meta.bodyKey, href: meta.href };
}
