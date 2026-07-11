"use client";

import { signIn } from "next-auth/react";
import { Logo } from "@/components/layout/logo";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

/**
 * Sign-in page — the only auth entry point for the closed beta (Google OAuth).
 * Public route; the middleware redirects here when an unauthenticated visitor
 * requests a protected page. On success NextAuth returns them to `callbackUrl`.
 */
export default function SignInPage() {
  const { t } = useTranslation();
  // In the Colab demo (dev login on) Google OAuth isn't configured, so showing "Continue with
  // Google" would only dead-end. In that mode we show ONLY the demo login; a normal build shows
  // only Google. This is build-time gated by NEXT_PUBLIC_DEV_LOGIN and never on in production.
  const demoMode = process.env.NEXT_PUBLIC_DEV_LOGIN === "1";
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

        {demoMode ? (
          <>
            <Button
              className="mt-6 w-full"
              size="lg"
              onClick={async () => {
                // redirect:false keeps NextAuth from building an absolute redirect to the server's
                // own origin (which is `localhost` behind a tunnel and unreachable from the browser).
                // The CSRF + callback requests are same-origin, so this works over the tunnel; we
                // navigate ourselves on success.
                const res = await signIn("dev", { redirect: false });
                if (res?.ok) window.location.assign("/");
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
              onClick={() => signIn("google", { callbackUrl: "/" })}
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
