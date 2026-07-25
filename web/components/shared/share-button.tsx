"use client";

import * as React from "react";
import { Check, Share2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

/**
 * Share the current page — the native share sheet where the platform has one, otherwise copy the
 * URL to the clipboard with a brief "Copied" confirmation. Entirely frontend: no share contract
 * exists in the engine, and none is needed for a URL.
 */
export function ShareButton({ title }: { title: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = React.useState(false);

  const share = React.useCallback(async () => {
    const url = window.location.href;
    try {
      if (navigator.share) {
        await navigator.share({ title, url });
        return;
      }
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* dismissed the sheet / clipboard denied — nothing to report */
    }
  }, [title]);

  return (
    <Button variant="outline" size="sm" onClick={share} aria-label={t("story.share")}>
      {copied ? <Check className="h-4 w-4" aria-hidden /> : <Share2 className="h-4 w-4" aria-hidden />}
      {copied ? t("common.copied") : t("story.share")}
    </Button>
  );
}
