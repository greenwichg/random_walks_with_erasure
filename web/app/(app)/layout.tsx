import { redirect } from "next/navigation";
import { Header } from "@/components/layout/header";
import { InstallPrompt } from "@/components/pwa/install-prompt";
import { FooterSlot, UtilityBarSlot } from "@/components/layout/chrome-slots";
import { PushReconciler } from "@/components/push/push-reconciler";
import { backendGet } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";
import { needsOnboarding, type OnboardingState } from "@/lib/onboarding";

/**
 * The authenticated app shell (Template-4): a sticky full-width masthead (wordmark + primary
 * nav on desktop, drawer + page label below lg) + the global utility strip + scrolling main +
 * the global footer, all sharing one centred content column. Every `(app)` page inherits the
 * full editorial chrome by being rendered here — pages no longer carry their own utility bar or
 * footer. The layout persists across route changes (App Router), so none of this chrome
 * remounts on navigation.
 *
 * The desktop shell was a fixed 256px sidebar until the desktop rework
 * (docs/DESKTOP_EDITORIAL_AUDIT.md, part 2): it spent a fifth of a 1280px screen on a directory,
 * named the page twice, and at 1024px left every companion rail too narrow to hold its own
 * stat boxes. The masthead gives the whole width back to the page.
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
      <Header />
      <UtilityBarSlot />
      {/* Below the header and above the page, on the same centred column as the page content.
          Renders nothing at all unless the browser has offered an install path and the reader
          has not dismissed one recently. */}
      <div className="mx-auto w-full max-w-7xl px-4 pt-4 sm:px-6 lg:px-8">
        <InstallPrompt />
      </div>
      <main className="min-h-[calc(100vh-4rem)]">{children}</main>
      <FooterSlot />
    </div>
  );
}
