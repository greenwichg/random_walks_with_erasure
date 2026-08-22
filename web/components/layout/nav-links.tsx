"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { NAV } from "@ih/core/logic/nav";
import { NAV_ICONS } from "@/lib/nav-icons";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";

/** The icon for a nav destination. The nav table itself is shared (@ih/core) and carries no icon. */
function NavIcon({ href, className }: { href: string; className?: string }) {
  const Icon = NAV_ICONS[href];
  return Icon ? <Icon className={className} /> : null;
}


function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

/** The sidebar's grouped nav list. Shared by the desktop rail and mobile drawer. */
export function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { t } = useTranslation();

  return (
    <nav className="flex flex-col gap-6 px-3">
      {NAV.map((section, i) => (
        <div key={i} className="flex flex-col gap-1">
          {section.title && (
            <p className="px-3 pb-1 text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground/70">
              {section.titleKey ? t(section.titleKey) : section.title}
            </p>
          )}
          {section.items.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onNavigate}
                className={cn(
                  "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "text-primary"
                    : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                )}
              >
                {active && (
                  <motion.span
                    layoutId="nav-active"
                    className="absolute inset-0 rounded-lg bg-accent"
                    transition={{ type: "spring", stiffness: 400, damping: 32 }}
                  />
                )}
                <NavIcon href={item.href} className="relative z-10 h-[1.15rem] w-[1.15rem]" />
                <span className="relative z-10">{t(item.labelKey)}</span>
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
