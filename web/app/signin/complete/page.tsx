"use client";

import * as React from "react";
import { Loader2 } from "lucide-react";
import { useSession } from "next-auth/react";
import { Logo } from "@/components/layout/logo";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import {
  clearPendingOnboarding,
  readPendingOnboarding,
} from "@/lib/onboarding";

/**
 * Where sign-in lands, before the app.
 *
 * A visitor picks outlets while anonymous, so the selection is stashed in the browser (there is no
 * account to attach it to yet) and only the browser can read it back. The app shell's onboarding gate
 * runs on the server. Those two facts are what make the ordering matter: land on `/` first and the
 * gate sees an account with no outlets and no reads, and sends a reader who just finished the funnel
 * back into it.
 *
 * So both sign-in providers return HERE instead of `/`. This page persists the stash and only then
 * moves on, which leaves the gate with nothing to special-case: by the time it runs, the store holds
 * the answer. It is deliberately the smallest possible step — no chrome, no data fetching, and for a
 * returning reader with nothing stashed, a straight pass-through.
 *
 * Sitting outside `(app)` is load-bearing: this page must not be gated by the thing it satisfies.
 */
export default function SignInCompletePage() {
  const { t } = useTranslation();
  const { status } = useSession();
  const [failed, setFailed] = React.useState(false);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    if (status === "loading") return;
    // No session (a shared link, an expired sign-in): nothing can be attributed, so hand back to the
    // middleware, which sends unauthenticated visitors to the funnel.
    if (status === "unauthenticated") {
      window.location.replace("/");
      return;
    }

    const outlets = readPendingOnboarding();
    if (!outlets) {
      window.location.replace("/");      // returning reader — pass straight through
      return;
    }

    let alive = true;
    fetch("/api/me/onboarding", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outlets }),
    })
      .then((res) => {
        if (!alive) return;
        if (!res.ok) throw new Error("save failed");
        clearPendingOnboarding();        // consumed exactly once, so this can never loop
        window.location.replace("/");
      })
      .catch(() => {
        // The stash is intentionally left in place: the reader can retry here, or complete the funnel
        // again, and either way their picks are still there.
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [status, attempt]);

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      {/* Sign-in itself requires JS, so this is belt-and-braces: never strand a reader on a spinner. */}
      <noscript>
        <meta httpEquiv="refresh" content="0;url=/" />
      </noscript>
      <div className="w-full max-w-sm rounded-2xl border bg-card p-8 text-center shadow-sm">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        {failed ? (
          <>
            <p role="alert" className="text-sm font-medium text-destructive">
              {t("onboarding.saveFailed")}
            </p>
            <Button className="mt-5 w-full" size="lg" onClick={() => { setFailed(false); setAttempt((n) => n + 1); }}>
              {t("common.tryAgain")}
            </Button>
            <a
              href="/onboarding"
              className="mt-3 inline-block w-full text-xs text-muted-foreground hover:text-foreground"
            >
              {t("onboarding.adjustOutlets")}
            </a>
          </>
        ) : (
          <>
            <Loader2 className="mx-auto h-8 w-8 animate-spin text-primary" />
            <p className="mt-4 text-sm text-muted-foreground">{t("signin.finishing")}</p>
          </>
        )}
      </div>
    </main>
  );
}
