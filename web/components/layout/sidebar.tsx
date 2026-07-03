import Link from "next/link";
import { HeartPulse } from "lucide-react";
import { Logo } from "@/components/layout/logo";
import { NavLinks } from "@/components/layout/nav-links";
import { ScrollArea } from "@/components/ui/scroll-area";

/** Fixed desktop sidebar rail (lg+). The mobile drawer reuses <NavLinks>. */
export function Sidebar() {
  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r bg-card/40 lg:flex">
      <div className="flex h-16 items-center px-6">
        <Link href="/" aria-label="Information Health home">
          <Logo />
        </Link>
      </div>
      <ScrollArea className="flex-1 py-2">
        <NavLinks />
      </ScrollArea>
      <div className="m-3 rounded-xl border bg-gradient-to-b from-accent/60 to-accent/10 p-4">
        <div className="mb-1.5 flex items-center gap-2 text-sm font-medium">
          <HeartPulse className="h-4 w-4 text-primary" />
          Reading streak
        </div>
        <p className="text-xs text-muted-foreground">
          12 days strong. Read one cross-cutting article today to keep it alive.
        </p>
      </div>
    </aside>
  );
}
