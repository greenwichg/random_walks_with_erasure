"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronDown } from "lucide-react";
import { NAV_DESKTOP_MENU, NAV_DESKTOP_PRIMARY } from "@ih/core/logic/nav";
import { NAV_ICONS } from "@/lib/nav-icons";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

// Tighter at lg (1024–1279px), where the masthead has ~960px for wordmark + seven items + the
// action cluster; measured at 1024 the roomier padding left the row 100px over budget.
const LINK =
  "relative inline-flex items-center gap-1 whitespace-nowrap px-2 text-sm font-medium transition-colors xl:px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring";

/** The current-section rule, drawn ON the header's bottom border. */
function ActiveRule() {
  return <span aria-hidden className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary xl:inset-x-3" />;
}

/**
 * The desktop masthead's primary navigation (lg+): six text links in a row and a "More" menu for
 * the records and tools, the current section marked by a rule sitting on the header's bottom
 * border — the way a news site's section bar marks where you are, rather than a filled pill.
 * Below lg the app keeps its drawer (nav-links.tsx) and this renders nothing, so the mobile
 * chrome is untouched — including the account menu, which is why the overflow lives here rather
 * than in it.
 *
 * Replaces the fixed 256px sidebar on desktop. That rail cost a fifth of a 1280px screen, said
 * the page's name twice (rail row + header label + the page's own h1), and at 1024px left the
 * story page's companion rail 230px wide. The six destinations here are the ones a reader
 * navigates BETWEEN (@ih/core/logic/nav — one source for labels, hints and order).
 */
export function DesktopNav() {
  const pathname = usePathname();
  const { t } = useTranslation();
  const moreActive = NAV_DESKTOP_MENU.some((item) => isActive(pathname, item.href));

  return (
    <nav aria-label={t("header.primaryNav")} className="hidden h-16 items-stretch lg:flex">
      {NAV_DESKTOP_PRIMARY.map((item) => {
        const active = isActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            title={item.hintKey ? t(item.hintKey) : undefined}
            className={cn(LINK, active ? "text-foreground" : "text-muted-foreground hover:text-foreground")}
          >
            {t(item.labelKey)}
            {active && <ActiveRule />}
          </Link>
        );
      })}

      {/* `modal={false}` for the same reason as the header's account menu: a navigation menu must
          not lock scroll or hide the document from assistive tech. */}
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(LINK, moreActive ? "text-foreground" : "text-muted-foreground hover:text-foreground")}
          >
            {t("nav.more")}
            <ChevronDown className="h-3.5 w-3.5 opacity-70" aria-hidden />
            {moreActive && <ActiveRule />}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          {NAV_DESKTOP_MENU.map((item) => {
            const Icon = NAV_ICONS[item.href];
            const active = isActive(pathname, item.href);
            return (
              <DropdownMenuItem key={item.href} asChild>
                <Link href={item.href} aria-current={active ? "page" : undefined} className={cn(active && "font-medium")}>
                  {Icon && <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />}
                  {t(item.labelKey)}
                </Link>
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    </nav>
  );
}
