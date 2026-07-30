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
import { jwtCallback, sessionCallback, signInCallback } from "./auth-callbacks.ts";
// Still used directly by the dev provider's authorize(), which keys the upsert on the EMAIL —
// not on what NextAuth later reports as account.providerAccountId (that is the engine user id).
import { upsertEngineUser } from "./engine-identity.ts";

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
  callbacks: { signIn: signInCallback, jwt: jwtCallback, session: sessionCallback },
};
