"use client";

import * as React from "react";
import { signIn } from "next-auth/react";
import { Logo } from "@/components/layout/logo";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import { track } from "@/lib/analytics";

/**
 * Sign-in page — the only auth entry point for the closed beta (Google OAuth).
 * Public route; the middleware redirects here when an unauthenticated visitor
 * requests a protected page. On success NextAuth returns them to `callbackUrl`.
 *
 * That callback URL is `/signin/complete`, never `/` directly: a visitor may have picked outlets
 * while anonymous, and those picks live in the browser until something authenticated persists them.
 * `/signin/complete` does exactly that and then moves on, so nobody reaches a gated page with an
 * account the store knows nothing about. For a returning reader it is a pass-through.
 */
/** Where every provider lands: the step that persists a pre-sign-in outlet selection. */
const POST_SIGNIN = "/signin/complete";

export default function SignInPage() {
  const { t } = useTranslation();
  // In the Colab demo (dev login on) Google OAuth isn't configured, so showing "Continue with
  // Google" would only dead-end. In that mode we show ONLY the demo login; a normal build shows
  // only Google. This is build-time gated by NEXT_PUBLIC_DEV_LOGIN and never on in production.
  const demoMode = process.env.NEXT_PUBLIC_DEV_LOGIN === "1";

  // BA1: NextAuth redirects a beta-allowlist rejection here as `?error=AccessDenied`. Show a friendly
  // invite-only message. Read from the URL on the client (no Suspense boundary needed).
  const [denied, setDenied] = React.useState(false);
  React.useEffect(() => {
    try {
      setDenied(new URLSearchParams(window.location.search).get("error") === "AccessDenied");
    } catch {
      /* ignore */
    }
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border bg-card p-8 shadow-sm">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <h1 className="text-center text-xl font-semibold tracking-tight text-balance">
          {t("signin.welcome")}
        </h1>
        <p className="mx-auto mt-2 max-w-xs text-center text-sm text-muted-foreground">
          {demoMode ? t("signin.demoSubtitle") : t("signin.subtitle")}
        </p>

        {denied && (
          <div
            role="alert"
            className="mt-5 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-left"
          >
            <p className="text-sm font-semibold text-amber-700 dark:text-amber-400">
              {t("signin.denied.title")}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">{t("signin.denied.body")}</p>
          </div>
        )}

        {demoMode ? (
          <>
            <Button
              className="mt-6 w-full"
              size="lg"
              onClick={async () => {
                track("signin_started", { method: "dev" });
                // redirect:false keeps NextAuth from building an absolute redirect to the server's
                // own origin (which is `localhost` behind a tunnel and unreachable from the browser).
                // The CSRF + callback requests are same-origin, so this works over the tunnel; we
                // navigate ourselves on success.
                const res = await signIn("dev", { redirect: false });
                if (res?.ok) window.location.assign(POST_SIGNIN);
              }}
            >
              {t("signin.continueDemo")}
            </Button>
            <p className="mt-3 text-center text-xs text-muted-foreground">{t("signin.demoNote")}</p>
          </>
        ) : (
          <>
            <Button
              className="mt-6 w-full"
              size="lg"
              onClick={() => {
                track("signin_started", { method: "google" });
                signIn("google", { callbackUrl: POST_SIGNIN });
              }}
            >
              {t("signin.continueGoogle")}
            </Button>
            <p className="mt-4 text-center text-xs text-muted-foreground">{t("signin.googleNote")}</p>
          </>
        )}
      </div>
    </main>
  );
}
