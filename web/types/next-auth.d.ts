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
}

declare module "next-auth/jwt" {
  interface JWT {
    engineUserId?: number;
  }
}
