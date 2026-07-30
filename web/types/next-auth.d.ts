/**
 * Module augmentation: carry the stable engine user id on the session and JWT so it
 * is available (and typed) everywhere NextAuth exposes them. Set by the callbacks in
 * `lib/auth.ts` once the identity has been upserted into the engine.
 */
import "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
  interface Session {
    /** Stable engine user id from `POST /api/internal/users`, when resolved. */
    engineUserId?: number;
  }
  interface User {
    /** Resolved by the dev credentials provider's `authorize` (dev-only sign-in). */
    engineUserId?: number;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    engineUserId?: number;
    /**
     * Which third-party identity this token belongs to, recorded at sign-in.
     *
     * Optional because tokens minted before these claims existed do not carry them and stay valid;
     * anything reading them must tolerate their absence. They live on the JWT only — the `Session`
     * above is what reaches the browser, and deliberately does not carry them.
     */
    provider?: string;
    /** Google only, where it is the value the engine keys the identity on. */
    providerAccountId?: string;
  }
}
