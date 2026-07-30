"use client";

import * as React from "react";
import { Loader2 } from "lucide-react";
import { useSession } from "next-auth/react";
import { Logo } from "@/components/layout/logo";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import {
  clearPendingOnboarding,
  needsOnboarding,
  readPendingOnboarding,
  type OnboardingState,
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
 * the answer. For a returning reader with nothing stashed it is a pass-through.
 *
 * Sitting outside `(app)` is load-bearing: this page must not be gated by the thing it satisfies.
 *
 * IT IS CHECK-THEN-WRITE, NOT WRITE-AND-HOPE. `GET /api/me` runs first and the stash is landed only
 * when `needsOnboarding()` — the same predicate the gate uses — says the account has never been
 * initialized. That buys three things:
 *
 *   - **Real idempotency.** A refresh mid-write, a duplicated tab, or React's double-invoked effect
 *     finds the row already there and passes through, rather than relying on the write being an
 *     upsert.
 *   - **An established account is never overwritten.** A stash abandoned months ago in this browser
 *     would otherwise replace a real reader's outlets and — because the write also stores a fresh
 *     estimate snapshot, and `latest_report` returns the newest — demote their Measured report to an
 *     Estimate.
 *   - **The two decisions cannot diverge.** One predicate, so this page and the gate always agree
 *     about who is new.
 */

/** Bound on the whole check-then-write round trip, so a hung server can't leave a reader on a
 *  spinner forever. Comfortably above the engine's own 6 s timeout, which is the slowest legitimate
 *  outcome (the route surfaces that as a 503 and we show the retry card). */
const REQUEST_TIMEOUT_MS = 12_000;

/** Retries offered before the flow stops promising something that may be impossible. A failure the
 *  reader can fix (a selection the registry no longer knows, a sign-in that never resolved an engine
 *  identity) returns the same error however many times it is retried; the funnel is where it gets
 *  fixed, so that becomes the primary action. */
const MAX_ATTEMPTS = 2;

export default function SignInCompletePage() {
  const { t } = useTranslation();
  const { status } = useSession();
  const [failed, setFailed] = React.useState(false);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    if (status === "loading") return;
    // No session (a shared link, a sign-in that never completed): nothing can be attributed, so hand
    // back to the middleware, which sends unauthenticated visitors to the funnel.
    if (status === "unauthenticated") {
      leave();
      return;
    }

    const outlets = readPendingOnboarding();
    if (!outlets) {
      leave();                            // returning reader — pass straight through
      return;
    }

    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), REQUEST_TIMEOUT_MS);
    let alive = true;

    void (async () => {
      try {
        // Is this account actually uninitialized? An unreachable engine answers nothing, in which
        // case fall through to the write: a brand-new account is overwhelmingly the likely case here,
        // and the write is the reason we are on this page.
        const meRes = await fetch("/api/me", { signal: ctl.signal });
        if (meRes.ok) {
          const me = (await meRes.json()) as OnboardingState;
          if (!needsOnboarding(me)) {
            clearPendingOnboarding();     // the stash is stale; the account is already established
            leave();
            return;
          }
        }

        const res = await fetch("/api/me/onboarding", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ outlets }),
          signal: ctl.signal,
        });
        if (!res.ok) throw new Error(`save failed: ${res.status}`);
        clearPendingOnboarding();          // consumed, so a second pass has nothing to redo
        leave();
      } catch {
        // The stash is deliberately left in place: whether the reader retries here or completes the
        // funnel again, their picks are still there.
        if (alive) setFailed(true);
      } finally {
        clearTimeout(timer);
      }
    })();

    return () => {
      alive = false;
      ctl.abort();                         // no state updates, and no write racing a later attempt
      clearTimeout(timer);
    };
  }, [status, attempt]);

  const exhausted = attempt >= MAX_ATTEMPTS;
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
            {/* Retries first, then the funnel — a failure that survives two attempts is one the
                reader fixes by re-picking, not by waiting. Either way the stash survives. */}
            {exhausted ? (
              <Button asChild className="mt-5 w-full" size="lg">
                <a href="/onboarding">{t("onboarding.adjustOutlets")}</a>
              </Button>
            ) : (
              <>
                <Button
                  className="mt-5 w-full"
                  size="lg"
                  onClick={() => {
                    setFailed(false);
                    setAttempt((n) => n + 1);
                  }}
                >
                  {t("common.tryAgain")}
                </Button>
                <a
                  href="/onboarding"
                  className="mt-3 inline-block w-full text-xs text-muted-foreground hover:text-foreground"
                >
                  {t("onboarding.adjustOutlets")}
                </a>
              </>
            )}
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

/**
 * On to the app — as a document load that REPLACES this entry, not a client navigation.
 *
 * `replace` keeps the interstitial out of history, so Back from the dashboard cannot land on it (the
 * one thing that would make this step look like a loop). A full load rather than `router.replace`
 * because the gate's verdict must be computed after the write: a client navigation can be served from
 * the Router Cache, and a payload rendered before the row existed would redirect a reader who is now
 * perfectly onboarded.
 */
function leave(): void {
  window.location.replace("/");
}
