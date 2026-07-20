"use client";

import * as React from "react";
import { Info } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * A small "i" affordance with an explanatory tooltip — used on every metric and section card.
 *
 * Works on touch as well as hover: the tooltip is controlled, so hover/focus opens it (desktop) AND a
 * tap toggles it (mobile, where Radix tooltips never open on their own). The click stops propagation so
 * tapping the "i" inside a card that is itself a link never navigates that card.
 */
export function InfoTooltip({ text, className }: { text: string; className?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(false);
  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={t("common.moreInfo")}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setOpen((o) => !o);
          }}
          className={cn(
            "grid h-5 w-5 place-items-center rounded-full text-muted-foreground/60 transition-colors hover:bg-muted hover:text-muted-foreground",
            className,
          )}
        >
          <Info className="h-3.5 w-3.5" />
        </button>
      </TooltipTrigger>
      {/* Tapping outside closes it on touch (there is no pointer-leave on touch). */}
      <TooltipContent onPointerDownOutside={() => setOpen(false)}>{text}</TooltipContent>
    </Tooltip>
  );
}
