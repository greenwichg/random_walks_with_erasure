/**
 * The pre-sign-in onboarding stash — the web's half.
 *
 * A reader can pick outlets before signing in; the selection has to survive an OAuth round trip
 * through Google and back. `localStorage` is how the web does that, and it is the only reason this
 * file is not in @ih/core: a native client has no sign-in redirect to survive, and would reach for
 * `expo-secure-store` if it did.
 *
 * The shape and the "never initialized" predicate are shared — see `@ih/core/logic/onboarding`,
 * re-exported below so callers keep a single import.
 */
export { needsOnboarding, type OnboardingState } from "@ih/core/logic/onboarding";

/** localStorage key holding an onboarding selection made before the sign-in redirect. */
export const PENDING_ONBOARDING_KEY = "ih:pendingOnboarding";

/** The two facts `GET /api/me` carries about initialization. */
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
