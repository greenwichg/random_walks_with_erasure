"use client";

import { Info } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** A small "i" affordance with an explanatory tooltip — used on every metric. */
export function InfoTooltip({ text, className }: { text: string; className?: string }) {
  const { t } = useTranslation();
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={t("common.moreInfo")}
          className={cn(
            "grid h-5 w-5 place-items-center rounded-full text-muted-foreground/60 transition-colors hover:bg-muted hover:text-muted-foreground",
            className,
          )}
        >
          <Info className="h-3.5 w-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}
