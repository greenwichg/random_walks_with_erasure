"use client";

import * as React from "react";
import { useSession } from "next-auth/react";
import { PENDING_ONBOARDING_KEY, clearOnboardingPending } from "@/lib/onboarding";

/**
 * After Google sign-in, flush any onboarding selection saved to localStorage before the OAuth
 * redirect — persisting the user's first estimate to their new account. Runs once when a
 * session becomes available and clears the pending state on success (leaving it for a later
 * retry on failure). Renders nothing; mounted inside the authenticated app shell.
 *
 * Every path that clears the localStorage payload also withdraws the marker cookie that holds the
 * app shell's onboarding gate open (see `lib/onboarding.ts`) — including the "nothing stashed" case,
 * so a marker can never outlive the selection it stands for.
 */
export function OnboardingSync() {
  const { status } = useSession();
  const flushed = React.useRef(false);

  React.useEffect(() => {
    if (status !== "authenticated" || flushed.current) return;
    if (typeof window === "undefined") return;
    const raw = window.localStorage.getItem(PENDING_ONBOARDING_KEY);
    if (!raw) {
      clearOnboardingPending();       // a marker with no payload behind it: drop it
      return;
    }
    flushed.current = true;

    const done = () => {
      window.localStorage.removeItem(PENDING_ONBOARDING_KEY);
      clearOnboardingPending();
    };

    let outlets: string[] = [];
    try {
      const parsed = JSON.parse(raw) as { outlets?: string[] };
      outlets = Array.isArray(parsed.outlets) ? parsed.outlets : [];
    } catch {
      done();
      return;
    }
    if (outlets.length === 0) {
      done();
      return;
    }

    fetch("/api/me/onboarding", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outlets }),
    })
      .then((r) => {
        if (r.ok) done();
      })
      .catch(() => {
        /* leave the pending item AND the marker so a later visit can retry the save */
      });
  }, [status]);

  return null;
}
