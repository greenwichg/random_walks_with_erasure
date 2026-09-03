"use client";

import * as React from "react";
import Link from "next/link";
import { signOut } from "next-auth/react";
import { ChevronRight, Menu } from "lucide-react";
import { useDiscover, useSettings } from "@/hooks/use-data";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** How many catalog topics the menu lists before "Discover more topics" takes over. */
const TOPIC_LIMIT = 12;

const ROW =
  "flex w-full items-center justify-between gap-3 px-5 py-2.5 text-left text-[15px] leading-snug transition-colors hover:bg-accent focus-visible:outline-none focus-visible:bg-accent";

function Row({ href, onClick, children, chevron = true, onNavigate }: {
  href?: string;
  onClick?: () => void;
  children: React.ReactNode;
  chevron?: boolean;
  onNavigate?: () => void;
}) {
  const inner = (
    <>
      <span className="min-w-0 truncate">{children}</span>
      {chevron && <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />}
    </>
  );
  if (href) {
    return (
      <li>
        <Link href={href} onClick={onNavigate} className={ROW}>
          {inner}
        </Link>
      </li>
    );
  }
  return (
    <li>
      <button type="button" onClick={onClick} className={ROW}>
        {inner}
      </button>
    </li>
  );
}

function Divider() {
  return <li role="separator" className="my-1.5 border-t" />;
}

/**
 * The desktop slide-out menu (lg+) — the reference layout's left panel, opened from the masthead's
 * menu button: account rows first, then the reader's own surfaces, then tools, then the catalog's
 * topics as a chevron list, then records, then privacy. Every row is a real Hidden View route;
 * the topic rows come from the live catalog (`/api/discover` facets, fetched the first time the
 * panel opens), never a hardcoded desk list.
 *
 * Separate from the mobile drawer on purpose: that drawer (header.tsx + nav-links.tsx) renders the
 * grouped NAV with icons and is left exactly as it was. This panel is desktop chrome only.
 */
export function DesktopMenu() {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(false);
  const close = React.useCallback(() => setOpen(false), []);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" className="hidden lg:inline-flex" aria-label={t("header.openMenu")}>
          <Menu />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="safe-top w-[21rem] p-0">
        <SheetTitle className="sr-only">{t("header.primaryNav")}</SheetTitle>
        {open && <MenuBody onNavigate={close} />}
      </SheetContent>
    </Sheet>
  );
}

function MenuBody({ onNavigate }: { onNavigate: () => void }) {
  const { t } = useTranslation();
  const facets = useDiscover({});
  const settings = useSettings();
  const place = settings.data?.edition ?? settings.data?.locations?.find((l) => l.level === "country")?.placeId ?? null;
  const localHref = place ? `/stories?country=${encodeURIComponent(place)}` : "/stories";
  const topics = (facets.data?.topics ?? []).slice(0, TOPIC_LIMIT);

  return (
    <nav aria-label={t("header.primaryNav")} className="pb-6 pt-3">
      <ul className="flex flex-col">
        <Row href="/" chevron={false} onNavigate={onNavigate}>{t("nav.dashboard")}</Row>
        <Row href="/profile" chevron={false} onNavigate={onNavigate}>{t("home.menu.myAccount")}</Row>
        <Row chevron={false} onClick={() => signOut({ callbackUrl: "/signin" })}>{t("header.signOut")}</Row>
        <Divider />
        <Row href="/recommendations" onNavigate={onNavigate}>{t("nav.forYou")}</Row>
        <Row href="/report" onNavigate={onNavigate}>{t("nav.report")}</Row>
        <Row href="/coach" onNavigate={onNavigate}>{t("nav.coach")}</Row>
        <Divider />
        <Row href="/settings" onNavigate={onNavigate}>{t("nav.settings")}</Row>
        <Row href="/analyze" onNavigate={onNavigate}>{t("home.footer.analyze")}</Row>
        <Row href="/settings" onNavigate={onNavigate}>{t("home.utility.extension")}</Row>
        <Divider />
        <li className={cn("px-5 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground")}>
          {t("home.menu.topics")}
        </li>
        {topics.map((topic) => (
          <Row key={topic} href={`/stories?topic=${encodeURIComponent(topic)}`} onNavigate={onNavigate}>
            {topic}
          </Row>
        ))}
        <Row href={localHref} onNavigate={onNavigate}>{t("nav.local")}</Row>
        <Row href="/stories?blindspot=any" onNavigate={onNavigate}>{t("home.blindspots.title")}</Row>
        <Row href="/discover" onNavigate={onNavigate}>{t("home.menu.discoverMore")}</Row>
        <Divider />
        <Row href="/stories" onNavigate={onNavigate}>{t("nav.stories")}</Row>
        <Row href="/saved" onNavigate={onNavigate}>{t("nav.saved")}</Row>
        <Row href="/history" onNavigate={onNavigate}>{t("nav.history")}</Row>
        <Row href="/analytics" onNavigate={onNavigate}>{t("nav.analytics")}</Row>
        <Divider />
        <Row href="/privacy" chevron={false} onNavigate={onNavigate}>{t("home.footer.privacy")}</Row>
      </ul>
    </nav>
  );
}
