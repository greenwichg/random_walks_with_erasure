/**
 * The shared notification metadata table (architecture §3).
 *
 * Pure data: no icons, no components, no DOM, no runtime imports. That is what lets the React inbox,
 * the service worker, a `node --test` process and (via a generated artifact) a non-bundled worker
 * script all consume the same rows.
 *
 * **Why a table and not a shared `notificationPresentation()`.** The two consumers want different
 * types under the same field names — React wants `icon` to be a component, the Notification API wants
 * a URL to a raster image — they need disjoint extra fields (`tag`, `badge`, `actions`,
 * `requireInteraction` have no React counterpart), and decisively they need *different correct
 * behaviour for an unknown kind*: the inbox degrades to an inert row with no navigation, while a push
 * has already interrupted the reader and must be tappable. A shared function would have to pick one.
 *
 * Adding a kind is a row here plus a row in the engine's registry. Rendering stays per channel.
 */

export interface NotificationKindMeta {
  /** Catalog key for the title. Per-kind, not per-notification. */
  titleKey: string;
  /** Catalog key for the body, interpolated from the notification's payload; null ⇒ title only. */
  bodyKey: string | null;
  /** The kind's static destination; null ⇒ informational, no navigation. */
  href: string | null;
  /**
   * Payload field that, when present and usable, replaces `href` with a per-notification destination.
   * The engine mirrors this in `examples/push_payload.py::_DEEP_LINKS` — a drift between them sends a
   * reader somewhere the inbox would not, so a test compares the two.
   */
  deepLinkField: string | null;
  /** Path template for `deepLinkField`; `{}` is the escaped value. */
  deepLinkPath: string | null;
}

export const NOTIFICATION_KINDS: Record<string, NotificationKindMeta> = {
  // Both of these used to point at "/report" — the CURRENT full health report, which is neither
  // weekly nor monthly and is the same page the sidebar already offers. Clicking either
  // notification therefore landed the reader somewhere generic that said nothing about the period
  // it had just announced, and the two kinds were indistinguishable once you followed them.
  weekly_report: {
    titleKey: "notifications.weekly_report.title",
    bodyKey: "notifications.weekly_report.body",
    href: "/report/weekly",
    deepLinkField: null,
    deepLinkPath: null,
  },
  monthly_deep_dive: {
    titleKey: "notifications.monthly_deep_dive.title",
    bodyKey: "notifications.monthly_deep_dive.body",
    href: "/report/monthly",
    deepLinkField: null,
    deepLinkPath: null,
  },
  recommendations_waiting: {
    titleKey: "notifications.recommendations_waiting.title",
    bodyKey: "notifications.recommendations_waiting.body",
    href: "/recommendations",
    deepLinkField: null,
    deepLinkPath: null,
  },
  weekly_digest: {
    titleKey: "notifications.weekly_digest.title",
    bodyKey: "notifications.weekly_digest.body",
    href: "/",
    deepLinkField: null,
    deepLinkPath: null,
  },
  streak_reminder: {
    titleKey: "notifications.streak_reminder.title",
    bodyKey: "notifications.streak_reminder.body",
    href: "/",
    deepLinkField: null,
    deepLinkPath: null,
  },
  blind_spot_alert: {
    titleKey: "notifications.blind_spot_alert.title",
    bodyKey: "notifications.blind_spot_alert.body",
    href: "/report",
    deepLinkField: null,
    deepLinkPath: null,
  },
  breaking_story: {
    titleKey: "notifications.breaking_story.title",
    bodyKey: "notifications.breaking_story.body",
    href: "/stories",
    deepLinkField: "storyId",
    deepLinkPath: "/stories/",
  },
};

/** Keys used when a consumer meets a kind it has no row for. Never a raw key, never a crash. */
export const GENERIC_KIND: NotificationKindMeta = {
  titleKey: "notifications.generic.title",
  bodyKey: null,
  href: null,
  deepLinkField: null,
  deepLinkPath: null,
};

export function kindMeta(kind: string): NotificationKindMeta {
  return NOTIFICATION_KINDS[kind] ?? GENERIC_KIND;
}

/**
 * Where one notification navigates, from its metadata and payload.
 *
 * Shared because both consumers need the same answer; what differs is the FALLBACK, which each
 * supplies: the inbox passes through `href: null` as "no navigation", while the worker substitutes
 * the server-computed `href` from the payload so a push is always tappable (§6).
 */
export function hrefFor(kind: string, payload?: unknown): string | null {
  const meta = kindMeta(kind);
  if (meta.deepLinkField && meta.deepLinkPath) {
    const value = (payload as Record<string, unknown> | undefined)?.[meta.deepLinkField];
    if (typeof value === "string" && value.trim()) {
      return meta.deepLinkPath + encodeURIComponent(value.trim());
    }
  }
  return meta.href;
}

/**
 * Display label for the unread badge: 0 ⇒ `""` (hidden), 1–9 ⇒ the number, >9 ⇒ `"9+"`.
 *
 * Here rather than beside the web's icon map because the rule — where the count stops being a count
 * — is a product decision, and a mobile tab badge that said "12" while the web said "9+" would be
 * two answers to one question.
 */
export function badgeLabel(unread: number): string {
  if (!Number.isFinite(unread) || unread <= 0) return "";
  return unread > 9 ? "9+" : String(Math.floor(unread));
}
