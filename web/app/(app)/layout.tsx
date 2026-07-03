import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";

/** The authenticated app shell: fixed sidebar (lg+) + sticky header + scrolling main. */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <Sidebar />
      <div className="lg:pl-64">
        <Header />
        <main className="min-h-[calc(100vh-4rem)]">{children}</main>
      </div>
    </div>
  );
}
