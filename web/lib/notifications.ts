/**
 * Notification presentation (N3) — the pure, only mapping from a notification KIND to how the
 * header panel renders it (icon / localized title / localized body / in-app destination). Like
 * `rec-presentation`, it holds template keys as **string literals** so the check:i18n unused-key
 * scanner reads them straight from this file, and it has no React and no runtime imports (runs
 * under `node --test`).
 *
 * An **unknown kind** never crashes and never renders a raw key: it degrades to a safe generic row
 * (bell icon, a generic localized title, no body, no navigation). This is the single place that
 * knows kind-specific UI — components go through `notificationPresentation(kind)` for how a row
 * looks and `notificationHref(kind, payload)` for where it goes, and nowhere else.
 */
import type { LucideIcon } from "lucide-react";
import { Activity, CalendarDays, Sparkles, BarChart3, Flame, Eye, Zap, Bell } from "lucide-react";
import { kindMeta, hrefFor } from "./notification-kinds.ts";

export interface NotificationPresentation {
  icon: LucideIcon;
  /** Catalog key for the title (always defined; generic fallback for unknown kinds). */
  titleKey: string;
  /** Catalog key for the body, interpolated from the notification payload; null ⇒ title only. */
  bodyKey: string | null;
  /** In-app destination for a click; null ⇒ the row is informational (no navigation). */
  href: string | null;
}

/**
 * kind → ICON. The only per-kind fact that is React's alone: the shared metadata table holds the
 * template keys and destinations (`lib/notification-kinds.ts`), and the Notification API wants a URL
 * here rather than a component, which is precisely why the table does not carry it.
 */
const ICONS: Record<string, LucideIcon> = {
  weekly_report: Activity,
  monthly_deep_dive: CalendarDays,
  recommendations_waiting: Sparkles,
  weekly_digest: BarChart3,
  streak_reminder: Flame,
  blind_spot_alert: Eye,
  breaking_story: Zap,
};

/**
 * Where a notification should navigate, given its payload.
 *
 * Kept separate from {@link NotificationPresentation.href} rather than turning that field into a
 * function: every other kind has a genuinely static destination, and making all six carry a resolver
 * to serve one exception would be the wrong trade. Components call this; the static `href` remains
 * the answer when the payload has nothing better.
 *
 * Defensive because the payload is stored JSON that may predate any given shape: a `storyId` that is
 * missing, empty, or not a string falls back to the kind's static destination rather than building
 * `/stories/undefined`.
 */
export function notificationHref(kind: string, payload?: unknown): string | null {
  return hrefFor(kind, payload);
}

/** Resolve a notification kind to its presentation; an unknown kind gets the safe generic row. */
export function notificationPresentation(kind: string): NotificationPresentation {
  const meta = kindMeta(kind);
  return {
    icon: ICONS[kind] ?? Bell,
    titleKey: meta.titleKey,
    bodyKey: meta.bodyKey,
    href: meta.href,
  };
}

/** Display label for the unread badge: 0 ⇒ "" (hidden), 1–9 ⇒ the number, >9 ⇒ "9+". */
export function badgeLabel(unread: number): string {
  if (!Number.isFinite(unread) || unread <= 0) return "";
  return unread > 9 ? "9+" : String(Math.floor(unread));
}
