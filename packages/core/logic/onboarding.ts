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
 * What is HERE is the shared half: the account shape and the one definition of "never initialized".
 * The pre-sign-in stash — the localStorage key, the writer and the parser — is in
 * `web/lib/onboarding.ts`, because per-platform storage is per-platform: a native client would use
 * `expo-secure-store` or `AsyncStorage`, and it has no sign-in redirect to survive in the first
 * place. The predicate is the part two clients must agree on.
 */

export interface OnboardingState {
  onboarding?: { outlets: string[] } | null;
  reads?: number | null;
}

/**
 * "This account has never been initialized" — the ONE definition of it.
 *
 * Read by the app-shell gate (to decide whether to redirect) and by the sign-in landing step (to
 * decide whether a stashed selection is still wanted). Sharing the predicate is what makes the two
 * unable to disagree: a landing step that thought an account was fresh while the gate thought
 * otherwise is exactly the shape a redirect loop would take.
 *
 * `reads` matters because reading is onboarding in substance — an extension-first reader, or an
 * account created before the gate existed, has no row but is plainly established.
 */
export function needsOnboarding(me: OnboardingState): boolean {
  return !me.onboarding && (me.reads ?? 0) === 0;
}

/** Stash a selection for the landing page to persist after sign-in. Never throws. */
