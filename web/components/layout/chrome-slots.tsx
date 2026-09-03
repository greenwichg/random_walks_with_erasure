"use client";

import { usePathname } from "next/navigation";
import { UtilityBar } from "@/components/home/utility-bar";
import { SiteFooter } from "@/components/home/site-footer";
import { DesktopFooter } from "@/components/layout/desktop-footer";
import { TopicStrip } from "@/components/shared/topic-strip";
import { useDiscover } from "@/hooks/use-data";

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
  "mx-auto w-full max-w-6xl pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] sm:px-6 lg:px-8";

/** Below lg only: on desktop the masthead carries its own top strip (header.tsx). */
export function UtilityBarSlot() {
  const pathname = usePathname();
  if (immersive(pathname)) return null;
  return (
    <div className={`${GUTTERS} lg:hidden`}>
      <UtilityBar />
    </div>
  );
}

/**
 * The mobile topic strip — chrome, not page content: the reference carries it under the bar on
 * every screen, so it lives in the shell rather than in each page. Chips LINK here (a page-level
 * filter belongs to the page that owns a payload); the desktop home renders its own filtering
 * strip instead, which is why this one is `lg:hidden`.
 *
 * Topics come from the catalog facets the filters and the menu already fetch — a cached query,
 * not a new one for the layout's sake.
 */
export function TopicStripSlot() {
  const pathname = usePathname();
  const facets = useDiscover({});
  if (immersive(pathname)) return null;
  const topics = (facets.data?.topics ?? []).slice(0, 12).map((topic) => ({ topic, count: 0 }));
  if (topics.length === 0) return null;
  return <TopicStrip topics={topics} className="lg:hidden" />;
}

/** The mobile footer below lg, the reference-layout footer from lg — two components, one slot,
 *  so the mobile footer stays byte-for-byte what it was. */
export function FooterSlot() {
  const pathname = usePathname();
  if (immersive(pathname)) return null;
  return (
    <div className={`${GUTTERS} pb-[max(1.5rem,env(safe-area-inset-bottom))] lg:pb-8`}>
      <div className="lg:hidden">
        <SiteFooter />
      </div>
      <div className="hidden lg:block">
        <DesktopFooter />
      </div>
    </div>
  );
}
