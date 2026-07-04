"use client";

import { signIn } from "next-auth/react";
import { Logo } from "@/components/layout/logo";
import { Button } from "@/components/ui/button";

/**
 * Sign-in page — the only auth entry point for the closed beta (Google OAuth).
 * Public route; the middleware redirects here when an unauthenticated visitor
 * requests a protected page. On success NextAuth returns them to `callbackUrl`.
 */
export default function SignInPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border bg-card p-8 shadow-sm">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <h1 className="text-center text-xl font-semibold tracking-tight text-balance">
          Welcome to Information Health
        </h1>
        <p className="mx-auto mt-2 max-w-xs text-center text-sm text-muted-foreground">
          Sign in to save your reports and track how your reading diet changes over time.
        </p>
        <Button
          className="mt-6 w-full"
          size="lg"
          onClick={() => signIn("google", { callbackUrl: "/" })}
        >
          Continue with Google
        </Button>
        <p className="mt-4 text-center text-xs text-muted-foreground">
          We use your Google account only to sign you in. Your reading data stays private.
        </p>
      </div>
    </main>
  );
}
