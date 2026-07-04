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

const ENGINE_BASE = process.env.RWE_BACKEND_URL ?? "http://127.0.0.1:8000";

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
      headers: { "Content-Type": "application/json" },
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
  ],
  session: { strategy: "jwt" },
  pages: { signIn: "/signin" },
  callbacks: {
    async jwt({ token, account, profile }) {
      // `account` is present only on the initial sign-in — the one place we call the engine.
      if (account) {
        const engineUserId = await upsertEngineUser({
          provider: account.provider,
          providerAccountId: account.providerAccountId,
          email: (profile as { email?: string } | undefined)?.email ?? token.email,
          displayName: (profile as { name?: string } | undefined)?.name ?? token.name,
        });
        if (engineUserId != null) token.engineUserId = engineUserId;
      }
      return token;
    },
    async session({ session, token }) {
      if (typeof token.engineUserId === "number") session.engineUserId = token.engineUserId;
      return session;
    },
  },
};
