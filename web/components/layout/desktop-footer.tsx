"use client";

import * as React from "react";
import Link from "next/link";
import { Logo } from "@/components/layout/logo";
import { useTranslation } from "@/lib/i18n";

/**
 * The desktop footer (lg+), to the reference layout: a row of link columns under headings, a
 * rule, then the wordmark with the tagline, then the legal line. Every link is a route that
 * exists; the reference's app-store, careers and newsletter columns have no counterpart here and
 * are omitted rather than rendered dead. The mobile footer (site-footer.tsx) is untouched.
 */
const COLUMNS: { titleKey: string; links: { href: string; labelKey: string }[] }[] = [
  {
    titleKey: "home.footer.news",
    links: [
      { href: "/", labelKey: "nav.dashboard" },
      { href: "/stories", labelKey: "nav.stories" },
      { href: "/discover", labelKey: "nav.discover" },
      { href: "/stories?blindspot=any", labelKey: "home.blindspots.title" },
      { href: "/stories", labelKey: "nav.local" },
    ],
  },
  {
    titleKey: "home.footer.you",
    links: [
      { href: "/recommendations", labelKey: "nav.forYou" },
      { href: "/report", labelKey: "nav.report" },
      { href: "/analytics", labelKey: "nav.analytics" },
      { href: "/history", labelKey: "nav.history" },
      { href: "/saved", labelKey: "nav.saved" },
    ],
  },
  {
    titleKey: "home.footer.tools",
    links: [
      { href: "/analyze", labelKey: "home.footer.analyze" },
      { href: "/settings", labelKey: "home.utility.extension" },
      { href: "/coach", labelKey: "nav.coach" },
    ],
  },
  {
    titleKey: "nav.section.account",
    links: [
      { href: "/profile", labelKey: "nav.profile" },
      { href: "/settings", labelKey: "nav.settings" },
      { href: "/privacy", labelKey: "home.footer.privacy" },
    ],
  },
];

export function DesktopFooter() {
  const { t } = useTranslation();
  const [year, setYear] = React.useState<number | null>(null);
  React.useEffect(() => setYear(new Date().getFullYear()), []);

  return (
    <footer className="mt-12 border-t pt-8">
      <div className="grid grid-cols-4 gap-8">
        {COLUMNS.map((col) => (
          <nav key={col.titleKey} aria-label={t(col.titleKey)}>
            <h2 className="mb-3 font-sans text-[13px] font-semibold">{t(col.titleKey)}</h2>
            <ul className="space-y-2">
              {col.links.map((link) => (
                <li key={link.href + link.labelKey}>
                  <Link
                    href={link.href}
                    className="rounded text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  >
                    {t(link.labelKey)}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        ))}
      </div>

      <div className="mt-10 flex items-end justify-between gap-8 border-t pt-8">
        <div>
          <Logo className="scale-125 origin-left" />
          <p className="mt-5 max-w-[28ch] text-[13px] leading-relaxed text-muted-foreground">
            {t("home.footer.tagline")}
          </p>
        </div>
        <ul className="flex items-center gap-5 text-[12px] text-muted-foreground">
          <li><Link href="/privacy" className="hover:text-foreground">{t("home.footer.privacy")}</Link></li>
          <li><Link href="/settings" className="hover:text-foreground">{t("nav.settings")}</Link></li>
        </ul>
      </div>

      <p className="mt-8 border-t pt-4 text-[12px] text-muted-foreground">
        {year != null ? t("home.footer.rights", { year }) : null}
      </p>
    </footer>
  );
}
