import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { OnboardingSync } from "@/components/onboarding/onboarding-sync";
import { FooterSlot, UtilityBarSlot } from "@/components/layout/chrome-slots";
import { backendGet } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { PENDING_ONBOARDING_COOKIE } from "@/lib/onboarding";

/**
 * The authenticated app shell (Template-4): fixed sidebar (lg+) + sticky header + the global
 * utility strip + scrolling main + the global footer. Every `(app)` page inherits the full
 * editorial chrome by being rendered here — pages no longer carry their own utility bar or
 * footer. The layout persists across route changes (App Router), so none of this chrome
 * remounts on navigation.
 *
 * THE ONBOARDING GATE lives here, and here only.
 *
 * `/onboarding` is the funnel; `/signin` is its LAST step (the estimate screen navigates there
 * itself). But `/signin` is also reachable directly — from an `?error=AccessDenied` bounce, a
 * bookmark, or a beta invite link — and NextAuth returns those users to `callbackUrl: "/"`. They
 * then arrive in the app with no outlets and no reads, which is the state that made every
 * personalised surface fall back to another reader's data.
 *
 * Middleware answers "are you signed in?"; it cannot answer "have you onboarded?" without reading
 * the store. This layout is the one choke point every protected page already renders through, it is
 * a server component (so the redirect costs no flash), and `/onboarding` lives OUTSIDE `(app)` — so
 * a loop is structurally impossible rather than merely unlikely.
 *
 * FAILS OPEN, in two ways. If the engine cannot be reached we render the app; and a reader whose
 * selection is still in flight (the marker cookie — see `lib/onboarding.ts`) is let through so
 * `OnboardingSync` below can land it. The beta access gate fails CLOSED because letting the wrong
 * person in is the harm there; here the harm runs the other way — neither an engine blip nor a
 * client-side handoff may bounce a reader into a funnel they have already completed.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  // A selection stashed pre-sign-in isn't in the store yet, and localStorage is invisible from here.
  // The marker cookie is the only thing that makes the anonymous funnel's own handoff legible to a
  // server gate; OnboardingSync withdraws it as soon as the flush lands.
  const pending = cookies().get(PENDING_ONBOARDING_COOKIE) !== undefined;

  // While a flush is pending the answer cannot change the outcome, so don't even ask.
  // `onboarding` is absent, not null, when unset (the engine serialises /api/me with
  // response_model_exclude_none), hence the falsy check rather than `=== null`.
  const me = pending
    ? null
    : await backendGet<{ onboarding?: { outlets: string[] } | null; reads?: number }>(
        "/api/me",
        await engineAuthHeaders(),
      );
  // `reads` is what keeps established readers out of the funnel: anyone with reading has onboarded
  // in substance whether or not a row exists (the e2e suite has been seeding one for exactly this
  // reason). Only a reader with NEITHER is genuinely un-onboarded — and `me === null` means we never
  // got an answer (unreachable engine, or the pending short-circuit above), which is not a "no".
  const unonboarded = me !== null && !me.onboarding && (me.reads ?? 0) === 0;
  if (unonboarded) redirect("/onboarding");

  return (
    <div className="min-h-screen">
      {/* Persist any pre-sign-in onboarding selection to the now-authenticated account. */}
      <OnboardingSync />
      <Sidebar />
      <div className="lg:pl-64">
        <Header />
        <UtilityBarSlot />
        <main className="min-h-[calc(100vh-4rem)]">{children}</main>
        <FooterSlot />
      </div>
    </div>
  );
}
