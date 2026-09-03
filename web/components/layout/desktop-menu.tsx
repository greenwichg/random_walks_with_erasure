"use client";

import * as React from "react";
import { Menu } from "lucide-react";
import { MenuPanel } from "@/components/layout/menu-panel";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useTranslation } from "@/lib/i18n";

/**
 * The desktop slide-out menu (lg+) — the masthead's menu button and the shared directory panel
 * (`MenuPanel`), which the mobile full-screen menu renders too. This file owns only the host:
 * a left sheet at panel width.
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
        {open && <MenuPanel onNavigate={close} />}
      </SheetContent>
    </Sheet>
  );
}
