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

export interface NotificationPresentation {
  icon: LucideIcon;
  /** Catalog key for the title (always defined; generic fallback for unknown kinds). */
  titleKey: string;
  /** Catalog key for the body, interpolated from the notification payload; null ⇒ title only. */
  bodyKey: string | null;
  /** In-app destination for a click; null ⇒ the row is informational (no navigation). */
  href: string | null;
}

const GENERIC: NotificationPresentation = {
  icon: Bell,
  titleKey: "notifications.generic.title",
  bodyKey: null,
  href: null,
};

/** kind → presentation. Literal template keys on purpose (check:i18n scans them). */
const MAP: Record<string, NotificationPresentation> = {
  weekly_report: {
    icon: Activity, href: "/report",
    titleKey: "notifications.weekly_report.title", bodyKey: "notifications.weekly_report.body",
  },
  monthly_deep_dive: {
    icon: CalendarDays, href: "/report",
    titleKey: "notifications.monthly_deep_dive.title", bodyKey: "notifications.monthly_deep_dive.body",
  },
  recommendations_waiting: {
    icon: Sparkles, href: "/recommendations",
    titleKey: "notifications.recommendations_waiting.title",
    bodyKey: "notifications.recommendations_waiting.body",
  },
  weekly_digest: {
    icon: BarChart3, href: "/",
    titleKey: "notifications.weekly_digest.title", bodyKey: "notifications.weekly_digest.body",
  },
  streak_reminder: {
    icon: Flame, href: "/",
    titleKey: "notifications.streak_reminder.title", bodyKey: "notifications.streak_reminder.body",
  },
  blind_spot_alert: {
    icon: Eye, href: "/report",
    titleKey: "notifications.blind_spot_alert.title", bodyKey: "notifications.blind_spot_alert.body",
  },
  // The first kind whose destination is per-notification rather than per-kind: every other row
  // opens a fixed page, this one opens the story that broke. `href` stays the static fallback
  // (`/stories`) so an old row with no `storyId` still navigates somewhere sensible;
  // `notificationHref` below resolves the specific one.
  breaking_story: {
    icon: Zap, href: "/stories",
    titleKey: "notifications.breaking_story.title", bodyKey: "notifications.breaking_story.body",
  },
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
  const { href } = notificationPresentation(kind);
  if (kind !== "breaking_story") return href;
  const storyId = (payload as { storyId?: unknown } | undefined)?.storyId;
  return typeof storyId === "string" && storyId.trim()
    ? `/stories/${encodeURIComponent(storyId.trim())}`
    : href;
}

/** Resolve a notification kind to its presentation; an unknown kind gets the safe generic row. */
export function notificationPresentation(kind: string): NotificationPresentation {
  return MAP[kind] ?? GENERIC;
}

/** Display label for the unread badge: 0 ⇒ "" (hidden), 1–9 ⇒ the number, >9 ⇒ "9+". */
export function badgeLabel(unread: number): string {
  if (!Number.isFinite(unread) || unread <= 0) return "";
  return unread > 9 ? "9+" : String(Math.floor(unread));
}
