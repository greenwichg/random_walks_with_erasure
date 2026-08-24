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
import { kindMeta, hrefFor, bodyKeyFor } from "@ih/core/logic/notification-kinds";

// Re-exported so the one existing consumer keeps a single import; the rule itself is shared.
export { badgeLabel } from "@ih/core/logic/notification-kinds";

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

/** The body key one notification renders — payload-aware (the weekly digest gains its scored
 *  variant when the engine's payload carries a measured `overall`). One rule, shared with the
 *  service worker via the kinds table; see `bodyKeyFor`. */
export function notificationBodyKey(kind: string, payload?: unknown): string | null {
  return bodyKeyFor(kindMeta(kind), payload);
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

