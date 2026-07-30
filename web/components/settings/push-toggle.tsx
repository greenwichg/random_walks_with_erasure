"use client";

/**
 * The per-DEVICE push control (B1).
 *
 * It sits beside the category preferences but is not one of them, and the distinction is the whole
 * reason it is its own component: a category preference says *what* a reader wants to hear about and
 * lives in their account; this says *whether this browser may show notifications* and lives in the
 * browser. Signing in elsewhere carries the first and not the second, so this row deliberately does
 * not participate in the settings draft, the Save button, or the dirty state — it takes effect
 * immediately, because a permission prompt cannot be staged and then cancelled.
 *
 * Four states, from `pushUiState`. `blocked` is the one that earns its own copy: once a reader denies
 * notifications the browser makes `requestPermission()` a permanent no-op, so an enable control there
 * would be a button that cannot work.
 */
import * as React from "react";
import { BellRing, Loader2 } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { usePush } from "@/hooks/use-push";
import { useTranslation } from "@/lib/i18n";

export function PushToggle() {
  const { t } = useTranslation();
  const { state, busy, failed, enable, disable } = usePush();

  // Unavailable is rendered as ABSENT, not as a disabled row: a browser without the APIs, or a
  // deployment with no VAPID key, has nothing the reader can act on, and a permanently greyed switch
  // reads as a fault in the product rather than a fact about the platform.
  if (state === "unavailable") return null;

  const blocked = state === "blocked";
  const description = blocked
    ? t("settings.notif.pushBlockedDesc")
    : failed
      ? t("settings.notif.pushFailedDesc")
      : t("settings.notif.pushDesc");

  return (
    <div className="flex items-center justify-between gap-4 py-4 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-start gap-3">
        <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <BellRing className="h-4 w-4" aria-hidden />
          )}
        </span>
        <div className="min-w-0">
          <p className="text-sm font-medium">{t("settings.notif.push")}</p>
          <p
            className={`mt-0.5 text-sm ${failed && !blocked ? "text-destructive" : "text-muted-foreground"}`}
          >
            {description}
          </p>
        </div>
      </div>
      <Switch
        checked={state === "on"}
        disabled={busy || blocked}
        onCheckedChange={(v) => void (v ? enable() : disable())}
        aria-label={t("settings.notif.push")}
      />
    </div>
  );
}
