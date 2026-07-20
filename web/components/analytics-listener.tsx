"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import { track } from "@/lib/analytics";

/**
 * Ambient product-analytics events (PA1): `app_opened` (once per browsing session), `page_viewed`
 * (every route), and `login_success` / `account_created` when a session becomes authenticated.
 * Rendered once inside the app providers; renders nothing. Every emission is best-effort and guarded,
 * so it can never affect what the user sees.
 */
export function AnalyticsListener() {
  const pathname = usePathname();
  const { status } = useSession();

  // app_opened — the first mount of a browsing session
  React.useEffect(() => {
    try {
      if (typeof window !== "undefined" && !window.sessionStorage.getItem("ih_app_opened")) {
        window.sessionStorage.setItem("ih_app_opened", "1");
        track("app_opened", {
          path: window.location.pathname,
          referrer: document.referrer || undefined,
        });
      }
    } catch {
      /* ignore */
    }
  }, []);

  // page_viewed — every route change (incl. the first render)
  React.useEffect(() => {
    if (pathname) track("page_viewed", { path: pathname });
  }, [pathname]);

  // login_success (once per session) + account_created (once per browser) on authentication
  React.useEffect(() => {
    if (status !== "authenticated") return;
    try {
      if (typeof window === "undefined" || window.sessionStorage.getItem("ih_login_sent")) return;
      window.sessionStorage.setItem("ih_login_sent", "1");
      track("login_success", { method: "google" });
      if (!window.localStorage.getItem("ih_signed_in_before")) {
        window.localStorage.setItem("ih_signed_in_before", "1");
        track("account_created", { method: "google" });
      }
    } catch {
      /* ignore */
    }
  }, [status]);

  return null;
}
