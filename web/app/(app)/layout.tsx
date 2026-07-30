import { redirect } from "next/navigation";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { FooterSlot, UtilityBarSlot } from "@/components/layout/chrome-slots";
import { backendGet } from "@/lib/backend";
import { engineAuthHeaders } from "@/lib/engine-auth";

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
  // `onboarding` is absent, not null, when unset (the engine serialises /api/me with
  // response_model_exclude_none), hence the falsy check rather than `=== null`.
  const me = await backendGet<{ onboarding?: { outlets: string[] } | null; reads?: number }>(
    "/api/me",
    await engineAuthHeaders(),
  );
  // `reads` is what keeps established readers out of the funnel: anyone with reading has onboarded
  // in substance whether or not a row exists (the e2e suite has been seeding one for exactly this
  // reason). Only a reader with NEITHER is genuinely un-onboarded — and `me === null` means we never
  // got an answer (unreachable engine), which is not a "no".
  const unonboarded = me !== null && !me.onboarding && (me.reads ?? 0) === 0;
  if (unonboarded) redirect("/onboarding");

  return (
    <div className="min-h-screen">
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
