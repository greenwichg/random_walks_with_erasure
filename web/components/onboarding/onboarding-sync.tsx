"use client";

import * as React from "react";
import { useSession } from "next-auth/react";

/** localStorage key holding an onboarding selection made before the OAuth redirect. */
export const PENDING_ONBOARDING_KEY = "ih:pendingOnboarding";

/**
 * After Google sign-in, flush any onboarding selection saved to localStorage before the OAuth
 * redirect — persisting the user's first estimate to their new account. Runs once when a
 * session becomes available and clears the pending state on success (leaving it for a later
 * retry on failure). Renders nothing; mounted inside the authenticated app shell.
 */
export function OnboardingSync() {
  const { status } = useSession();
  const flushed = React.useRef(false);

  React.useEffect(() => {
    if (status !== "authenticated" || flushed.current) return;
    if (typeof window === "undefined") return;
    const raw = window.localStorage.getItem(PENDING_ONBOARDING_KEY);
    if (!raw) return;
    flushed.current = true;

    let outlets: string[] = [];
    try {
      const parsed = JSON.parse(raw) as { outlets?: string[] };
      outlets = Array.isArray(parsed.outlets) ? parsed.outlets : [];
    } catch {
      window.localStorage.removeItem(PENDING_ONBOARDING_KEY);
      return;
    }
    if (outlets.length === 0) {
      window.localStorage.removeItem(PENDING_ONBOARDING_KEY);
      return;
    }

    fetch("/api/me/onboarding", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outlets }),
    })
      .then((r) => {
        if (r.ok) window.localStorage.removeItem(PENDING_ONBOARDING_KEY);
      })
      .catch(() => {
        /* leave the pending item so a later visit can retry the save */
      });
  }, [status]);

  return null;
}
