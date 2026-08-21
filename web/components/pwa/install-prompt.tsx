"use client";

import * as React from "react";
import { X, Share, Plus } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import {
  installOffer,
  isIosSafari,
  readDismissed,
  writeDismissed,
  type InstallOffer,
} from "@/lib/install-prompt";

/** The `beforeinstallprompt` event, which TypeScript's DOM lib still does not declare. */
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

/**
 * "Install Hidden View" — the native browser install flow, never a hand-rolled one.
 *
 * On Chromium the browser fires `beforeinstallprompt` when the site is installable; we hold that
 * event and replay it on click, which opens the REAL install dialog. Nothing here can install
 * anything by itself, and that is correct: a page that could would be a page that could install
 * itself without asking.
 *
 * iOS Safari never fires the event — WebKit has no programmatic install — so the honest fallback
 * is to describe the Share → Add to Home Screen path, and only on iOS Safari (`isIosSafari`), never
 * in a browser that has a real button.
 *
 * Every decision about whether to appear lives in `lib/install-prompt.ts` so it can be tested.
 */
export function InstallPrompt() {
  const { t } = useTranslation();
  const [offer, setOffer] = React.useState<InstallOffer>("hidden");
  const deferred = React.useRef<BeforeInstallPromptEvent | null>(null);

  const decide = React.useCallback((nativeReady: boolean) => {
    const standalone =
      window.matchMedia?.("(display-mode: standalone)").matches ||
      // iOS Safari's own flag; not standard, and only present there.
      (window.navigator as { standalone?: boolean }).standalone === true;
    setOffer(
      installOffer({
        installed: Boolean(standalone),
        nativePromptReady: nativeReady,
        iosSafari: isIosSafari(navigator.userAgent, navigator.maxTouchPoints),
        dismissedAt: readDismissed(),
        now: Date.now(),
      }),
    );
  }, []);

  React.useEffect(() => {
    decide(false);

    const onBeforeInstall = (e: Event) => {
      // Suppress Chrome's own mini-infobar so there is one install affordance, not two.
      e.preventDefault();
      deferred.current = e as BeforeInstallPromptEvent;
      decide(true);
    };
    const onInstalled = () => {
      deferred.current = null;
      setOffer("hidden");
    };

    window.addEventListener("beforeinstallprompt", onBeforeInstall);
    // Fires the moment the install completes, so the banner disappears without a reload.
    window.addEventListener("appinstalled", onInstalled);
    // A reader can also install from the browser's own menu, which changes display-mode with no
    // event of ours firing. Watching the media query catches that.
    const mq = window.matchMedia?.("(display-mode: standalone)");
    const onDisplayChange = () => decide(deferred.current !== null);
    mq?.addEventListener?.("change", onDisplayChange);

    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstall);
      window.removeEventListener("appinstalled", onInstalled);
      mq?.removeEventListener?.("change", onDisplayChange);
    };
  }, [decide]);

  const dismiss = () => {
    writeDismissed(Date.now());
    setOffer("hidden");
  };

  const install = async () => {
    const evt = deferred.current;
    if (!evt) return;
    deferred.current = null;
    try {
      await evt.prompt();
      const { outcome } = await evt.userChoice;
      // A declined install is a dismissal: re-offering on the next page view would be nagging.
      if (outcome !== "accepted") writeDismissed(Date.now());
    } catch {
      /* the event can only be replayed once; if it throws there is nothing to recover */
    }
    setOffer("hidden");
  };

  if (offer === "hidden") return null;

  return (
    <div
      role="region"
      aria-label={t("pwa.install.title")}
      className="mx-auto mb-4 flex w-full max-w-2xl items-center gap-3 rounded-xl border bg-card p-3 shadow-soft sm:gap-4 sm:p-4"
    >
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary/10 sm:h-11 sm:w-11">
        {/* The app's own mark, so the banner shows what the home-screen icon will look like.
            Same reasoning as CountryBadge: next/image adds a loader and layout machinery for no
            benefit on a static SVG, and would need `dangerouslyAllowSVG` to serve one at all. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/icon.svg" alt="" aria-hidden width={22} height={22} />
      </span>

      {/* min-w-0 so a long translation wraps inside the row instead of pushing the dismiss off. */}
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold leading-tight">{t("pwa.install.title")}</p>
        <p className="mt-0.5 text-xs leading-snug text-muted-foreground">
          {offer === "native" ? t("pwa.install.subtitle") : t("pwa.install.iosSubtitle")}
        </p>
        {offer === "ios-instructions" && (
          <p className="mt-1.5 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
            <Share className="h-3.5 w-3.5" aria-hidden />
            <span>{t("pwa.install.iosShare")}</span>
            <span aria-hidden>→</span>
            <Plus className="h-3.5 w-3.5" aria-hidden />
            <span>{t("pwa.install.iosAdd")}</span>
          </p>
        )}
      </div>

      {offer === "native" && (
        <button
          onClick={install}
          className="shrink-0 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          {t("pwa.install.action")}
        </button>
      )}

      <button
        onClick={dismiss}
        aria-label={t("pwa.install.dismiss")}
        className="shrink-0 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
