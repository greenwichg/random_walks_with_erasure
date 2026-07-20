/**
 * NextAuth (Auth.js v4) configuration — Google sign-in with stateless JWT sessions.
 *
 * On first sign-in the `jwt` callback upserts the Google identity into the engine
 * (`POST /api/internal/users`) and stores the returned stable engine user id on the
 * token, so every later request can be attributed to a real user without a second
 * database in the web tier. Sessions are JWT (no adapter/DB here) — the engine owns
 * all durable user state.
 *
 * Runtime env (see `.env.example`): GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
 * NEXTAUTH_SECRET, NEXTAUTH_URL. Absent credentials only disable live sign-in; the
 * app still builds and the engine still serves the demo reader.
 */
import type { NextAuthOptions } from "next-auth";
import GoogleProvider from "next-auth/providers/google";
import CredentialsProvider from "next-auth/providers/credentials";
import { isEmailAllowed } from "@/lib/beta-access";

const ENGINE_BASE = process.env.RWE_BACKEND_URL ?? "http://127.0.0.1:8000";

// Dev-only demo sign-in. OFF unless RWE_DEV_LOGIN is explicitly set (e.g. the Colab demo), and
// force-OFF whenever production mode is on — so it can NEVER be available in a real deployment,
// even if the flag leaks into the environment. It lets a reviewer explore the full signed-in app
// without Google OAuth by signing in as a throwaway demo account (an unauthenticated login path by
// design). RWE_ENV=production is the cross-tier production switch (the engine reads the same
// variable to fail closed); the Colab demo does NOT set it, so the demo login keeps working there
// even though Colab serves a production Next build (NODE_ENV=production).
const PRODUCTION = process.env.RWE_ENV === "production" || process.env.RWE_ENV === "prod";
const DEV_LOGIN =
  !PRODUCTION && (process.env.RWE_DEV_LOGIN === "1" || process.env.RWE_DEV_LOGIN === "true");

/**
 * Map a third-party identity to the stable engine user id, or `null` if the engine
 * is unreachable — in which case the app simply falls back to the demo reader.
 */
async function upsertEngineUser(input: {
  provider: string;
  providerAccountId: string;
  email?: string | null;
  displayName?: string | null;
}): Promise<number | null> {
  try {
    const res = await fetch(`${ENGINE_BASE}/api/internal/users`, {
      method: "POST",
      // The engine's /api/internal/* surface is fail-closed in production: it trusts a call only when
      // it carries the shared secret as X-IH-Auth (same header engineAuthHeaders sends for /api/me/*).
      // Without it this sign-in upsert 401s in prod, engineUserId never resolves, and every per-user
      // page falls through to a 401. Mirror lib/engine-auth.ts's internalSecretHeaders(): send the
      // secret when configured, omit it in dev (RWE_INTERNAL_SECRET unset) where the engine is open.
      headers: {
        "Content-Type": "application/json",
        ...(process.env.RWE_INTERNAL_SECRET
          ? { "X-IH-Auth": process.env.RWE_INTERNAL_SECRET }
          : {}),
      },
      cache: "no-store",
      body: JSON.stringify({
        provider: input.provider,
        providerAccountId: input.providerAccountId,
        email: input.email ?? undefined,
        displayName: input.displayName ?? undefined,
      }),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { userId?: number };
    return typeof data.userId === "number" ? data.userId : null;
  } catch {
    return null; // engine down at sign-in time — resolve to demo until it recovers
  }
}

export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID ?? "",
      clientSecret: process.env.GOOGLE_CLIENT_SECRET ?? "",
    }),
    // Present only when RWE_DEV_LOGIN is set. Upserts a stable throwaway demo user in the engine and
    // resolves its engine id here, so the dev session flows through the exact same /api/me/* path a
    // real user does — no special-casing downstream.
    ...(DEV_LOGIN
      ? [
          CredentialsProvider({
            id: "dev",
            name: "Demo reader (dev)",
            credentials: {
              name: { label: "Name", type: "text" },
              email: { label: "Email", type: "text" },
            },
            async authorize(credentials) {
              const name = (credentials?.name || "Demo Reader").toString().slice(0, 100);
              const email = (credentials?.email || "demo@infodiet.local").toString().slice(0, 200);
              const engineUserId = await upsertEngineUser({
                provider: "dev",
                providerAccountId: email,
                email,
                displayName: name,
              });
              if (engineUserId == null) return null; // engine down -> sign-in fails cleanly
              return { id: String(engineUserId), name, email, engineUserId };
            },
          }),
        ]
      : []),
  ],
  session: { strategy: "jwt" },
  // `error` routes NextAuth's AccessDenied (a beta-allowlist rejection) back to the sign-in page,
  // which shows a friendly invite-only message (?error=AccessDenied).
  pages: { signIn: "/signin", error: "/signin" },
  callbacks: {
    // BA1 — beta access control. Gate Google sign-in behind the approved-email allowlist so a
    // non-approved user never gets a session (and therefore never an engine account). The dev demo
    // login (non-production only) is not gated. Returning `false` triggers the AccessDenied redirect.
    // Access control only — no recommendation-engine, analytics, observability, or business-logic change.
    async signIn({ user, account, profile }) {
      if (account?.provider !== "google") return true; // dev credentials provider (dev-only) is exempt
      const email = user?.email ?? (profile as { email?: string } | undefined)?.email ?? null;
      const { allowed, reason } = isEmailAllowed(email);
      if (!allowed) {
        // OBS1-style structured line so the operator sees denied attempts (and who to approve). No
        // analytics/PA1 event is added; a denied user never reaches the authenticated tracking path.
        // eslint-disable-next-line no-console
        console.warn(JSON.stringify({ event: "beta_access_denied", email: email ?? null, reason }));
        return false;
      }
      return true;
    },
    async jwt({ token, account, profile, user }) {
      // Google: upsert the identity into the engine on the initial sign-in (the one engine call).
      if (account?.provider === "google") {
        const engineUserId = await upsertEngineUser({
          provider: account.provider,
          providerAccountId: account.providerAccountId,
          email: (profile as { email?: string } | undefined)?.email ?? token.email,
          displayName: (profile as { name?: string } | undefined)?.name ?? token.name,
        });
        if (engineUserId != null) token.engineUserId = engineUserId;
      }
      // Dev credentials sign-in: the engine id was resolved in authorize() and rides on `user`.
      if (typeof user?.engineUserId === "number") token.engineUserId = user.engineUserId;
      return token;
    },
    async session({ session, token }) {
      if (typeof token.engineUserId === "number") session.engineUserId = token.engineUserId;
      return session;
    },
  },
};
