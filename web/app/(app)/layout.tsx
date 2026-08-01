import { redirect } from "next/navigation";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { FooterSlot, UtilityBarSlot } from "@/components/layout/chrome-slots";
import { PushReconciler } from "@/components/push/push-reconciler";
import { backendGet } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { needsOnboarding, type OnboardingState } from "@/lib/onboarding";

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
 * The gate needs NO exception for a selection made before sign-in. That could have been a grace
 * window or a client-set marker the server has to take on trust; instead it is ordering —
 * `/signin/complete` persists an anonymous pick before anyone reaches a gated page (see
 * `lib/onboarding.ts`), so the store is the only thing this has to read.
 *
 * FAILS OPEN. If the engine cannot be reached we render the app. The beta access gate fails CLOSED
 * because letting the wrong person in is the harm there; here the harm runs the other way — an
 * engine blip must never bounce every signed-in reader into a funnel they have already completed.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await backendGet<OnboardingState>("/api/me", await engineAuthHeaders());
  // `needsOnboarding` is shared with `/signin/complete`, which decides from the same two facts whether
  // a pre-sign-in selection still wants landing — one predicate, so the two cannot disagree about who
  // is new. `me === null` means we never got an answer (unreachable engine), which is not a "no".
  if (me !== null && needsOnboarding(me)) redirect("/onboarding");

  return (
    <div className="min-h-screen">
      {/* Renders nothing. Here rather than in the settings page because the two desynchronisations
          it repairs — a VAPID rotation, a `410` prune — happen while the reader is anywhere but
          Settings, and this layout is the one client boundary every authenticated page passes
          through. It persists across route changes, so it runs once per app load, not per navigation. */}
      <PushReconciler />
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
