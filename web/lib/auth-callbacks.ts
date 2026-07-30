/**
 * The NextAuth callbacks, kept out of `lib/auth.ts`.
 *
 * Moved verbatim; the reason is testability, and it is not cosmetic. `lib/auth.ts` constructs the
 * providers at module load, and `next-auth/providers/*` are CommonJS — importing that module from bare
 * `node --test` fails on the CJS/ESM default-interop that webpack and tsc paper over. So as long as the
 * callbacks live beside the provider construction, they cannot be unit-tested at all. Here they can,
 * and `lib/auth.test.ts` exercises each one directly.
 *
 * The callbacks are where a session gets — and, when the engine was unreachable at sign-in, fails to
 * get — its engine identity, so they are worth covering: see docs/SESSION_IDENTITY_RECOVERY_DESIGN.md.
 */
import type { NextAuthOptions } from "next-auth";

import { isEmailAllowed } from "./beta-access.ts";
import { upsertEngineUser } from "./engine-identity.ts";

type Callbacks = NonNullable<NextAuthOptions["callbacks"]>;

// BA1 — beta access control. Gate Google sign-in behind the approved-email allowlist so a
// non-approved user never gets a session (and therefore never an engine account). The dev demo
// login (non-production only) is not gated. Returning `false` triggers the AccessDenied redirect.
// Access control only — no recommendation-engine, analytics, observability, or business-logic change.
export const signInCallback: Callbacks["signIn"] = async function signIn({ user, account, profile }) {
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
};

export const jwtCallback: Callbacks["jwt"] = async function jwt({ token, account, profile, user }) {
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

  // Record WHICH identity this token belongs to. `account` is present only on the sign-in
  // invocation, so this runs once per session and existing tokens are untouched (they keep
  // working — see docs/SESSION_IDENTITY_RECOVERY_DESIGN.md §9a).
  //
  // `providerAccountId` is stored for Google only, because there it is the same value the engine
  // keys the identity on. For the credentials provider NextAuth sets `account.providerAccountId`
  // to `user.id`, which `authorize()` sets to the ENGINE USER ID — not the email the dev upsert
  // keys on — so storing it under this name would promise something it is not. `provider` is
  // recorded either way, which is what lets a reader of this token know not to treat it as Google.
  if (account) {
    token.provider = account.provider;
    if (account.provider === "google") token.providerAccountId = account.providerAccountId;
  }
  return token;
};

export const sessionCallback: Callbacks["session"] = async function session({ session, token }) {
  if (typeof token.engineUserId === "number") session.engineUserId = token.engineUserId;
  return session;
};
