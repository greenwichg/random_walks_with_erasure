/**
 * The onboarding handoff — the one thing that has to survive a sign-in round trip.
 *
 * An anonymous visitor picks outlets BEFORE an account exists, so the selection is stashed here and
 * landed once a session appears. That stash is client-only, which matters because the app shell gates
 * on server-side onboarding state (`app/(app)/layout.tsx`): a reader who has just finished the funnel
 * and signed in would arrive at `/` with the selection still in the browser and no row in the store.
 *
 * The fix is ordering, not extra state: sign-in returns to `/signin/complete`, which lands the stash
 * BEFORE anyone reaches a gated page. The gate therefore needs no exception, no grace window, and
 * nothing to take on trust — by the time it runs, the store is the only source of truth again.
 *
 * These helpers are the whole contract: one key, one shape, one parser. Both the funnel (writer) and
 * the landing page (reader) go through them so the shape can't drift.
 */

/** localStorage key holding an onboarding selection made before the sign-in redirect. */
export const PENDING_ONBOARDING_KEY = "ih:pendingOnboarding";

/** Stash a selection for the landing page to persist after sign-in. Never throws. */
export function stashPendingOnboarding(outlets: string[]): void {
  try {
    window.localStorage.setItem(PENDING_ONBOARDING_KEY, JSON.stringify({ outlets }));
  } catch {
    /* private mode / quota — sign-in still proceeds, the reader just re-picks */
  }
}

/**
 * The stashed outlet ids, or `null` when there is nothing usable to land.
 *
 * Anything malformed — bad JSON, a non-array, an empty list, non-string members — reads as `null`
 * rather than throwing or half-succeeding, because the caller's only two branches are "persist this"
 * and "carry on".
 */
export function readPendingOnboarding(): string[] | null {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(PENDING_ONBOARDING_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { outlets?: unknown };
    const outlets = Array.isArray(parsed?.outlets)
      ? parsed.outlets.filter((o): o is string => typeof o === "string" && o.length > 0)
      : [];
    return outlets.length > 0 ? outlets : null;
  } catch {
    return null;
  }
}

/** Drop the stash — it has been persisted, or it was unusable. Never throws. */
export function clearPendingOnboarding(): void {
  try {
    window.localStorage.removeItem(PENDING_ONBOARDING_KEY);
  } catch {
    /* ignore */
  }
}
