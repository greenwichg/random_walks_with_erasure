/**
 * The onboarding handoff — the one thing that has to survive an OAuth round trip.
 *
 * An anonymous visitor picks outlets BEFORE an account exists, so the selection is stashed in
 * localStorage and flushed by `OnboardingSync` once a session appears. localStorage is invisible to
 * the server, which matters because the app shell now gates on server-side onboarding state
 * (`app/(app)/layout.tsx`): a reader who has just completed the funnel and signed in arrives at `/`
 * with the selection still client-side and no row in the store yet. The gate would bounce them back
 * into the funnel they just finished.
 *
 * So the funnel also drops a **marker cookie** — no payload, just "a flush is pending" — which the
 * server gate can read. The cookie is short-lived and cleared the moment the flush lands, so a
 * failed or abandoned flush re-arms the gate on its own rather than leaving a permanent hole.
 *
 * Both constants and both helpers live here, in a module with no "use client" directive, because
 * the server gate and the client flow each need one half of the pair.
 */

/** localStorage key holding an onboarding selection made before the sign-in redirect. */
export const PENDING_ONBOARDING_KEY = "ih:pendingOnboarding";

/** Marker cookie telling the server-side gate that a selection is stashed client-side. */
export const PENDING_ONBOARDING_COOKIE = "ih_pending_onboarding";

/** How long the gate will wait for the flush. Long enough for a slow OAuth round trip and a
 *  retry, short enough that an abandoned selection cannot hold the gate open. */
const PENDING_TTL_SECONDS = 30 * 60;

/** Announce a stashed selection to the server gate. Client-only; a no-op without `document`. */
export function markOnboardingPending(): void {
  if (typeof document === "undefined") return;
  const secure = typeof location !== "undefined" && location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    `${PENDING_ONBOARDING_COOKIE}=1; Path=/; Max-Age=${PENDING_TTL_SECONDS}; SameSite=Lax${secure}`;
}

/** Withdraw the marker — the flush landed (or there was nothing to flush). */
export function clearOnboardingPending(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${PENDING_ONBOARDING_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}
