"use client";

import * as React from "react";
import Link from "next/link";
import { signOut } from "next-auth/react";
import { ChevronRight } from "lucide-react";
import { useDiscover } from "@/hooks/use-data";
import { useLocalHref } from "@/lib/use-local-href";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** How many catalog topics the menu lists before "Discover more topics" takes over. */
const TOPIC_LIMIT = 12;

const ROW =
  "flex w-full items-center justify-between gap-3 px-5 py-3 text-left text-[15px] leading-snug transition-colors hover:bg-accent focus-visible:outline-none focus-visible:bg-accent";

function Row({
  href,
  onClick,
  children,
  chevron = true,
  onNavigate,
}: {
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
        <Link href={href} onClick={onNavigate} className={cn(ROW, "touch-target")}>
          {inner}
        </Link>
      </li>
    );
  }
  return (
    <li>
      <button type="button" onClick={onClick} className={cn(ROW, "touch-target")}>
        {inner}
      </button>
    </li>
  );
}

function Divider() {
  return <li role="separator" className="my-1.5 border-t" />;
}

/**
 * THE menu body — the reference layout's directory panel, rendered identically by the desktop
 * slide-out (`DesktopMenu`) and the mobile full-screen menu (`MobileMenu`). One list, two hosts:
 * account rows, the reader's own surfaces, tools, the catalog's topics, the records, privacy.
 *
 * Every row is a real Hidden View route and the topics come from the live catalog
 * (`/api/discover` facets, fetched the first time a panel opens) — never a hardcoded desk list.
 */
export function MenuPanel({ onNavigate }: { onNavigate: () => void }) {
  const { t } = useTranslation();
  const facets = useDiscover({});
  const localHref = useLocalHref();
  const topics = (facets.data?.topics ?? []).slice(0, TOPIC_LIMIT);

  return (
    <nav aria-label={t("header.primaryNav")} className="pb-8 pt-2">
      <ul className="flex flex-col">
        <Row href="/" chevron={false} onNavigate={onNavigate}>{t("nav.dashboard")}</Row>
        <Row href="/profile" chevron={false} onNavigate={onNavigate}>{t("home.menu.myAccount")}</Row>
        <Row href="/report" chevron={false} onNavigate={onNavigate}>{t("home.myBias.title")}</Row>
        <Row chevron={false} onClick={() => signOut({ callbackUrl: "/signin" })}>{t("header.signOut")}</Row>
        <Divider />
        <Row href="/recommendations" onNavigate={onNavigate}>{t("nav.forYou")}</Row>
        <Row href="/coach" onNavigate={onNavigate}>{t("nav.coach")}</Row>
        <Row href="/analytics" onNavigate={onNavigate}>{t("nav.analytics")}</Row>
        <Divider />
        <Row href="/settings" onNavigate={onNavigate}>{t("nav.settings")}</Row>
        <Row href="/analyze" onNavigate={onNavigate}>{t("home.footer.analyze")}</Row>
        <Row href="/settings" onNavigate={onNavigate}>{t("home.utility.extension")}</Row>
        <Divider />
        <li className="px-5 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("home.menu.topics")}
        </li>
        {topics.map((topic) => (
          <Row key={topic} href={`/stories?topic=${encodeURIComponent(topic)}`} onNavigate={onNavigate}>
            {topic}
          </Row>
        ))}
        <Row href={localHref} onNavigate={onNavigate}>{t("nav.local")}</Row>
        <Row href="/stories?blindspot=any" onNavigate={onNavigate}>{t("home.blindspots.title")}</Row>
        <Row href="/topics" onNavigate={onNavigate}>{t("home.menu.discoverMore")}</Row>
        <Divider />
        <Row href="/stories" onNavigate={onNavigate}>{t("nav.stories")}</Row>
        <Row href="/saved" onNavigate={onNavigate}>{t("nav.saved")}</Row>
        <Row href="/history" onNavigate={onNavigate}>{t("nav.history")}</Row>
        <Divider />
        <Row href="/privacy" chevron={false} onNavigate={onNavigate}>{t("home.footer.privacy")}</Row>
      </ul>
    </nav>
  );
}
