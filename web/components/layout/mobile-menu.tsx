"use client";

import * as React from "react";
import { Menu, X } from "lucide-react";
import { MenuPanel } from "@/components/layout/menu-panel";
import { Button } from "@/components/ui/button";
import { Sheet, SheetClose, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useTranslation } from "@/lib/i18n";

/**
 * The mobile menu (below lg) — the reference layout's full-screen directory: the panel fills the
 * viewport, "Home" heads it, and a close button sits opposite. Same rows as the desktop slide-out
 * (`MenuPanel`), because it is the same directory; only the host differs.
 *
 * This REPLACES the old icon-list drawer (nav-links.tsx) as the mobile menu. The bottom tab bar
 * now carries the five destinations a reader moves between, so the menu is the full directory
 * rather than a second copy of the primary nav.
 */
export function MobileMenu() {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(false);
  const close = React.useCallback(() => setOpen(false), []);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" className="lg:hidden" aria-label={t("header.openMenu")}>
          <Menu />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" hideClose className="safe-top w-screen max-w-none p-0">
        <div className="flex h-14 items-center justify-between border-b px-5">
          <SheetTitle className="text-[15px] font-semibold">{t("nav.dashboard")}</SheetTitle>
          <SheetClose asChild>
            <Button variant="ghost" size="icon" aria-label={t("common.close")}>
              <X />
            </Button>
          </SheetClose>
        </div>
        {open && <MenuPanel onNavigate={close} />}
      </SheetContent>
    </Sheet>
  );
}
