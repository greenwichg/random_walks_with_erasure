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
  /**
   * Optional richer body used when the payload carries a real `overall` score — selection rule in
   * `bodyKeyFor`, applied identically by the inbox and the service worker (data, not code, so the
   * generated worker table carries it too). The engine includes `overall` only when a measured
   * report exists, so the plain `bodyKey` remains the honest copy for readers below the
   * measurement threshold.
   */
  bodyScoredKey?: string;
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
  /**
   * How long the deep link stays usable, for kinds whose destination dissolves (a breaking story
   * ages out of the catalog window; its `/stories/st_…` then answers only "Story not found").
   * Freshness is judged from the payload: `expiresAt` (the engine event's own cutoff, carried by
   * the fan-out) when present, else `occurredAt` + these hours (rows materialised before
   * `expiresAt` was carried). Past either cutoff `hrefFor` falls back to the kind's static
   * `href` — the row stays clickable into something alive instead of a dead end. `null`/absent ⇒
   * the deep link never expires. Mirrors the engine's `story_events.ttl_hours` default; a parity
   * test compares the two.
   */
  deepLinkFreshHours?: number | null;
}

export const NOTIFICATION_KINDS: Record<string, NotificationKindMeta> = {
  // Both of these used to point at "/report" — the CURRENT full health report, which is neither
  // weekly nor monthly and is the same page the sidebar already offers. Clicking either
  // notification therefore landed the reader somewhere generic that said nothing about the period
  // it had just announced, and the two kinds were indistinguishable once you followed them.
  // LEGACY RENDER ONLY (2026-08-24): the engine no longer generates weekly_report — it was a
  // functional duplicate of weekly_digest (same week, same batch, same destination, its one
  // display fact a subset of the digest's payload) and the two were merged into the digest,
  // which now carries the score. The row stays so the notifications already sitting in readers'
  // inboxes keep their title, body, and click-through instead of degrading to the generic row.
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
  // The digest ("Your week in review — {reads} reads · {streakDays}-day streak") pointed at "/",
  // the same generic landing the two report kinds above escaped: Home says nothing about the week
  // the notification just summarized. The weekly period page IS that week — reading-over-time and
  // the health score windowed to the same seven days the digest's payload (reads, streakDays,
  // overall) describes. streak_reminder keeps "/" deliberately: its ask is "go read something
  // now", and the feed is where that happens.
  weekly_digest: {
    titleKey: "notifications.weekly_digest.title",
    bodyKey: "notifications.weekly_digest.body",
    // The merged weekly notification (2026-08-24): when the engine's payload carries a measured
    // score, the body says so — the fact the retired weekly_report kind existed to announce.
    bodyScoredKey: "notifications.weekly_digest.bodyScored",
    href: "/report/weekly",
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
    deepLinkFreshHours: 6,
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
 * Which body key one notification renders, from its metadata and payload: the scored variant when
 * the kind declares one AND the payload carries a real `overall`, else the plain body. The ONE
 * selection rule — the inbox applies it here and the service worker applies the same two-line rule
 * over the generated table (`public/sw.js`), so a push and its inbox row can never disagree about
 * what the week said.
 */
export function bodyKeyFor(meta: NotificationKindMeta, payload?: unknown): string | null {
  const p = payload as Record<string, unknown> | null | undefined;
  if (meta.bodyScoredKey && p && p["overall"] != null) return meta.bodyScoredKey;
  return meta.bodyKey;
}

/**
 * Where one notification navigates, from its metadata and payload.
 *
 * Shared because both consumers need the same answer; what differs is the FALLBACK, which each
 * supplies: the inbox passes through `href: null` as "no navigation", while the worker substitutes
 * the server-computed `href` from the payload so a push is always tappable (§6).
 */
export function hrefFor(kind: string, payload?: unknown, now?: Date): string | null {
  const meta = kindMeta(kind);
  if (meta.deepLinkField && meta.deepLinkPath) {
    const value = (payload as Record<string, unknown> | undefined)?.[meta.deepLinkField];
    if (typeof value === "string" && value.trim() && deepLinkFresh(meta, payload, now)) {
      return meta.deepLinkPath + encodeURIComponent(value.trim());
    }
  }
  return meta.href;
}

/**
 * Whether a kind's deep link is still worth following (see `deepLinkFreshHours`). The payload's
 * own `expiresAt` — the engine event's authoritative cutoff — wins when present and parseable;
 * rows materialised before it was carried are judged by `occurredAt` + the kind's hours. A kind
 * without `deepLinkFreshHours`, or a payload with neither timestamp, never expires — exactly the
 * pre-expiry behaviour, so no other kind changes.
 */
function deepLinkFresh(meta: NotificationKindMeta, payload: unknown, now?: Date): boolean {
  const hours = meta.deepLinkFreshHours;
  if (hours == null) return true;
  const p = payload as Record<string, unknown> | undefined;
  const at = (now ?? new Date()).getTime();
  const expires = typeof p?.expiresAt === "string" ? Date.parse(p.expiresAt) : NaN;
  if (!Number.isNaN(expires)) return at < expires;
  const occurred = typeof p?.occurredAt === "string" ? Date.parse(p.occurredAt) : NaN;
  if (!Number.isNaN(occurred)) return at - occurred < hours * 3_600_000;
  return true;
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
