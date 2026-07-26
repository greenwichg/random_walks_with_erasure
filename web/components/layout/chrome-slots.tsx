"use client";

import { usePathname } from "next/navigation";
import { UtilityBar } from "@/components/home/utility-bar";
import { SiteFooter } from "@/components/home/site-footer";

/**
 * Global chrome slots (Template-4): the utility strip and the site footer, rendered ONCE in the
 * app shell so every page inherits them — previously each page had to remember to render its own,
 * and only Home and Story Details did.
 *
 * Route-aware: immersive routes (the Coach chat, which pins its composer with a
 * `h-[calc(100vh-4rem)]` column) opt out of both bars — global chrome under a pinned-composer
 * layout would push the input below the fold.
 *
 * The horizontal wrapper mirrors PageContainer's gutters (incl. safe-area insets) so the chrome
 * lines up with every page's content column.
 */
const IMMERSIVE_ROUTES = ["/coach"];

function immersive(pathname: string | null): boolean {
  return IMMERSIVE_ROUTES.some((r) => pathname === r || pathname?.startsWith(`${r}/`));
}

const GUTTERS =
  "mx-auto w-full max-w-7xl pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] sm:px-6 lg:px-8";

export function UtilityBarSlot() {
  const pathname = usePathname();
  if (immersive(pathname)) return null;
  return (
    <div className={GUTTERS}>
      <UtilityBar />
    </div>
  );
}

export function FooterSlot() {
  const pathname = usePathname();
  if (immersive(pathname)) return null;
  return (
    <div className={`${GUTTERS} pb-[max(1.5rem,env(safe-area-inset-bottom))] lg:pb-8`}>
      <SiteFooter />
    </div>
  );
}
