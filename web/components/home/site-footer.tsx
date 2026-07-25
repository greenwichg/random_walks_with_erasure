"use client";

import * as React from "react";
import Link from "next/link";
import { Logo } from "@/components/layout/logo";
import { useTranslation } from "@/lib/i18n";

/**
 * The site footer — a secondary navigation surface at the end of a long editorial page.
 *
 * Every link points at a route that exists in this app. The masthead footers this page is modelled
 * on carry Careers / Press / Apps / Countries columns; those are omitted rather than rendered as
 * dead links, which is the same rule the utility bar follows.
 */
const COLUMNS: { titleKey: string; links: { href: string; labelKey: string }[] }[] = [
  {
    titleKey: "home.footer.product",
    links: [
      { href: "/", labelKey: "nav.dashboard" },
      { href: "/report", labelKey: "nav.report" },
      { href: "/recommendations", labelKey: "nav.recommendations" },
      { href: "/coach", labelKey: "nav.coach" },
    ],
  },
  {
    titleKey: "nav.section.explore",
    links: [
      { href: "/stories", labelKey: "nav.stories" },
      { href: "/discover", labelKey: "nav.discover" },
      { href: "/saved", labelKey: "nav.saved" },
      { href: "/history", labelKey: "nav.history" },
      { href: "/analytics", labelKey: "nav.analytics" },
    ],
  },
  {
    titleKey: "nav.section.account",
    links: [
      { href: "/profile", labelKey: "nav.profile" },
      { href: "/settings", labelKey: "nav.settings" },
      { href: "/analyze", labelKey: "home.footer.analyze" },
      { href: "/privacy", labelKey: "home.footer.privacy" },
    ],
  },
];

export function SiteFooter() {
  const { t } = useTranslation();
  // Rendered after mount for the same reason the utility bar defers its date: the current year is
  // resolved from the viewer's clock, and an SSR/client disagreement would be a hydration error.
  const [year, setYear] = React.useState<number | null>(null);
  React.useEffect(() => setYear(new Date().getFullYear()), []);

  return (
    <footer className="mt-14 border-t pt-10">
      <div className="grid grid-cols-2 gap-8 sm:grid-cols-4">
        <div className="col-span-2 sm:col-span-1">
          <Logo />
          <p className="mt-3 max-w-[22ch] text-xs leading-relaxed text-muted-foreground">
            {t("home.footer.tagline")}
          </p>
        </div>

        {COLUMNS.map((col) => (
          <nav key={col.titleKey} aria-label={t(col.titleKey)}>
            <h2 className="mb-2.5 text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground/70">
              {t(col.titleKey)}
            </h2>
            <ul className="space-y-1.5">
              {col.links.map((link) => (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    className="rounded text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  >
                    {t(link.labelKey)}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        ))}
      </div>

      <p className="mt-9 border-t pt-5 text-xs text-muted-foreground">
        {year != null ? t("home.footer.rights", { year }) : null}
      </p>
    </footer>
  );
}
