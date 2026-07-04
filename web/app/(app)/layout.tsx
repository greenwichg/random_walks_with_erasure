import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { OnboardingSync } from "@/components/onboarding/onboarding-sync";

/** The authenticated app shell: fixed sidebar (lg+) + sticky header + scrolling main. */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      {/* Persist any pre-sign-in onboarding selection to the now-authenticated account. */}
      <OnboardingSync />
      <Sidebar />
      <div className="lg:pl-64">
        <Header />
        <main className="min-h-[calc(100vh-4rem)]">{children}</main>
      </div>
    </div>
  );
}
