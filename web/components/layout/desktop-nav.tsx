"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useSettings } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const LINK =
  "relative inline-flex items-center whitespace-nowrap px-3 text-[15px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring";

/**
 * The desktop masthead's four section links, to the reference layout: Home · For You · Local ·
 * Blind spots. Everything else lives in the slide-out menu (desktop-menu.tsx) and the account
 * menu, exactly as the reference keeps its own directory off the bar. Each link is a real Hidden
 * View surface: For You is the recommendation feed, Local is the Stories browser scoped to the
 * reader's edition (or unscoped until they pick one), Blind spots is the coverage-gap lens.
 *
 * Rendered by header.tsx inside a Suspense boundary: `useSearchParams` needs one, and it is
 * needed here because Local and Blind spots are the same route told apart by their query.
 */
export function DesktopNav() {
  const pathname = usePathname();
  const params = useSearchParams();
  const { t } = useTranslation();
  const settings = useSettings();
  const place =
    settings.data?.edition ??
    settings.data?.locations?.find((l) => l.level === "country")?.placeId ??
    null;

  const onStories = pathname === "/stories";
  const items = [
    { href: "/", label: t("nav.dashboard"), active: pathname === "/" },
    { href: "/recommendations", label: t("nav.forYou"), active: pathname.startsWith("/recommendations") },
    {
      href: place ? `/stories?country=${encodeURIComponent(place)}` : "/stories",
      label: t("nav.local"),
      active: onStories && params.has("country"),
    },
    {
      href: "/stories?blindspot=any",
      label: t("home.blindspots.title"),
      active: onStories && params.has("blindspot"),
    },
  ];

  return (
    <nav aria-label={t("header.primaryNav")} className="hidden h-16 items-stretch lg:flex">
      {items.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          aria-current={item.active ? "page" : undefined}
          className={cn(LINK, item.active ? "text-foreground" : "text-muted-foreground hover:text-foreground")}
        >
          {item.label}
          {item.active && (
            <span aria-hidden className="absolute inset-x-3 -bottom-px h-0.5 rounded-full bg-foreground" />
          )}
        </Link>
      ))}
    </nav>
  );
}
