"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The account section's tab strip — the reference's My Account / My News Bias / Alerts / Discover
 * Topics row, over the four surfaces Hidden View already has:
 *
 *   My account     → /profile   the reader's identity, streak and achievements
 *   My news bias   → /report    the Health Report: their measured viewpoint, topics and sources
 *   Alerts         → /alerts    what the product has flagged for them
 *   Discover topics→ /topics    browse and follow topics, places and sources
 *
 * One strip rendered by all four pages, so the section navigates as a section rather than four
 * pages that happen to be related. It scrolls horizontally on a narrow screen instead of wrapping,
 * which would push the page's own heading down a line on every phone.
 */
const TABS = [
  { href: "/profile", labelKey: "nav.profile" },
  { href: "/report", labelKey: "home.myBias.title" },
  { href: "/alerts", labelKey: "alerts.title" },
  { href: "/topics", labelKey: "topics.title" },
];

export function AccountTabs({ className }: { className?: string }) {
  const pathname = usePathname();
  const { t } = useTranslation();

  return (
    <nav
      aria-label={t("nav.section.account")}
      className={cn("-mx-4 mb-6 border-b px-4 sm:mx-0 sm:px-0", className)}
    >
      <ul className="flex gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {TABS.map((tab) => {
          const active = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
          return (
            <li key={tab.href} className="shrink-0">
              <Link
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "relative inline-flex h-11 items-center whitespace-nowrap px-3 text-[14px] font-medium transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                  active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t(tab.labelKey)}
                {active && (
                  <span aria-hidden className="absolute inset-x-3 -bottom-px h-0.5 rounded-full bg-primary" />
                )}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
