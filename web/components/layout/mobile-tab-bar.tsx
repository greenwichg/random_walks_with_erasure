"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { EyeOff, MapPin, Newspaper, Search, Sparkles } from "lucide-react";
import { useLocalHref } from "@/lib/use-local-href";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The mobile bottom tab bar (below lg) — the reference layout's five destinations, and the one
 * genuinely new piece of chrome the mobile recreation needs: the app had a drawer and nothing
 * else, so the surfaces a reader moves between most were always two taps away.
 *
 * The five are the same five the desktop masthead names, in the same order, so the two layouts
 * teach one product: Home · For You · Search · Blind spots · Local. Everything else stays in the
 * slide-out menu and the account menu.
 *
 * Sits above the home indicator (`safe-bottom`), hides from `lg` up, and every target clears 44px.
 * Rendered by the app shell, which also reserves the matching bottom padding so a page's last row
 * is never trapped under it.
 */
export function MobileTabBar() {
  const pathname = usePathname();
  const params = useSearchParams();
  const { t } = useTranslation();
  const localHref = useLocalHref();

  const onStories = pathname === "/stories";
  const items = [
    { href: "/", label: t("nav.dashboard"), icon: Newspaper, active: pathname === "/" },
    {
      href: "/recommendations",
      label: t("nav.forYou"),
      icon: Sparkles,
      active: pathname.startsWith("/recommendations"),
    },
    { href: "/search", label: t("header.search"), icon: Search, active: pathname.startsWith("/search") },
    {
      href: "/stories?blindspot=any",
      label: t("home.blindspots.title"),
      icon: EyeOff,
      active: onStories && params.has("blindspot"),
    },
    { href: localHref, label: t("nav.local"), icon: MapPin, active: onStories && params.has("country") },
  ];

  return (
    <nav
      aria-label={t("header.primaryNav")}
      className="glass safe-bottom fixed inset-x-0 bottom-0 z-30 border-t lg:hidden"
    >
      <ul className="flex items-stretch">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <li key={item.label} className="flex-1">
              <Link
                href={item.href}
                aria-current={item.active ? "page" : undefined}
                className={cn(
                  "flex h-14 flex-col items-center justify-center gap-1 px-1 text-[10px] font-medium transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                  item.active ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <Icon className={cn("h-5 w-5", item.active && "text-primary")} aria-hidden />
                <span className="max-w-full truncate">{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
