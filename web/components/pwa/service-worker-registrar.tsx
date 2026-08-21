"use client";

import * as React from "react";

/**
 * Registers the service worker, unconditionally.
 *
 * Until now the ONLY thing that registered it was `PushReconciler`, behind
 * `config.enabled` — which is `RWE_PUSH_ENABLED`, and production runs with it **0**. So in
 * production no worker was ever registered, and `beforeinstallprompt` cannot fire without one:
 * the app was uninstallable for a reason that had nothing to do with installation.
 *
 * This does not replace that call and does not change it. `register()` is idempotent for the same
 * URL and scope — the second caller resolves against the first registration rather than creating a
 * second worker — so push's own sequencing (register, await, then repair) is untouched, and this
 * component staying mounted costs nothing when push is on.
 *
 * Deliberately not gated on sign-in: installability is a property of the site, and the install
 * banner is offered on the public onboarding page too.
 */
export function ServiceWorkerRegistrar() {
  React.useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    // After load, not during it. Registration competes with the first paint's own requests, and
    // nothing on screen is waiting for a worker.
    const register = () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        // Unsupported, blocked by policy, or served from a scope that forbids it. The app works
        // exactly as before without a worker — it is simply not installable.
      });
    };
    if (document.readyState === "complete") register();
    else {
      window.addEventListener("load", register, { once: true });
      return () => window.removeEventListener("load", register);
    }
  }, []);

  return null;
}
