"use client";

import * as React from "react";
import Link from "next/link";
import { Puzzle } from "lucide-react";
import { useTranslation } from "@/lib/i18n";

/**
 * The slim utility strip above the masthead: today's date on the left, the browser-extension
 * entry point on the right.
 *
 * Deliberately narrow. Notifications, theme and the user menu already live in the app header —
 * repeating them here would be chrome for its own sake — and the Edition / Location / Help
 * utilities a news masthead usually carries have no backing feature in this product, so they are
 * omitted rather than rendered as dead controls.
 *
 * The date is computed after mount: it depends on the viewer's locale + timezone, so rendering it
 * during SSR would risk a hydration mismatch. Until then the row simply has no date.
 */
export function UtilityBar() {
  const { t } = useTranslation();
  const [today, setToday] = React.useState("");

  React.useEffect(() => {
    setToday(
      new Date().toLocaleDateString(undefined, {
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
      }),
    );
  }, []);

  return (
    <div className="flex h-9 items-center justify-between gap-4 border-b text-xs text-muted-foreground">
      {/* aria-live="off": the date is ambient context, not an update worth announcing. */}
      <time aria-live="off" className="truncate tabular-nums">
        {today}
      </time>
      <Link
        href="/settings"
        className="inline-flex shrink-0 items-center gap-1.5 rounded font-medium transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        <Puzzle className="h-3.5 w-3.5" aria-hidden />
        {t("home.utility.extension")}
      </Link>
    </div>
  );
}
