import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { OnboardingSync } from "@/components/onboarding/onboarding-sync";
import { FooterSlot, UtilityBarSlot } from "@/components/layout/chrome-slots";

/**
 * The authenticated app shell (Template-4): fixed sidebar (lg+) + sticky header + the global
 * utility strip + scrolling main + the global footer. Every `(app)` page inherits the full
 * editorial chrome by being rendered here — pages no longer carry their own utility bar or
 * footer. The layout persists across route changes (App Router), so none of this chrome
 * remounts on navigation.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
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
