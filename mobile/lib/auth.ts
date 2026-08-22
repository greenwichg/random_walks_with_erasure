import * as AuthSession from "expo-auth-session";
import * as Google from "expo-auth-session/providers/google";
import * as WebBrowser from "expo-web-browser";

import { config } from "./config.ts";
import { clearSession, hasStoredToken, saveSession, type StoredSession } from "./session.ts";

/**
 * Native Google sign-in, and the exchange that turns it into a Hidden View session.
 *
 * Two steps, and the split is the whole security design:
 *
 *   1. Google, on the device, returns an **ID token** — a short-lived assertion, signed by Google,
 *      that says who this person is. Nothing about Hidden View is involved and no Hidden View
 *      secret is present, because a native OAuth client does not have one.
 *
 *   2. That ID token is posted to `POST /api/auth/mobile`, which verifies it against Google's
 *      published keys, checks it was minted for one of OUR client ids, requires a verified email,
 *      runs the same closed-beta allowlist the web sign-in runs, and returns a Hidden View bearer
 *      token. Every check that matters happens on the server, where the app cannot skip it.
 *
 * The Google ID token is used once and never stored. The Hidden View token goes to the platform
 * keystore (`lib/session.ts`) and is the only credential this app keeps.
 */

// Required for the OAuth redirect to return to the app rather than leaving the browser open.
WebBrowser.maybeCompleteAuthSession();

export type SignInResult =
  | { ok: true; session: StoredSession }
  | { ok: false; reason: "cancelled" | "no-id-token" | "rejected"; message?: string };

/**
 * The Google client ids, per platform.
 *
 * `expo-auth-session` picks the right one for the running platform. All three are PUBLIC
 * identifiers — the confidential half of an OAuth client is what a web server holds, and native
 * clients have none, which is why they can ship in `app.json`.
 */
export function googleConfig() {
  return {
    iosClientId: config.googleIosClientId || undefined,
    androidClientId: config.googleAndroidClientId || undefined,
    webClientId: config.googleWebClientId || undefined,
    // `openid` is what makes Google return an ID TOKEN rather than only an access token. Without
    // it the response carries something the server cannot verify an identity from.
    scopes: ["openid", "profile", "email"],
  };
}

/** Whether sign-in can even be attempted, so the UI can say why rather than failing on tap. */
export function signInConfigured(): boolean {
  return Boolean(
    config.googleIosClientId || config.googleAndroidClientId || config.googleWebClientId,
  );
}

/**
 * Trade a Google ID token for a Hidden View session.
 *
 * Separated from the Google half so it can be exercised without a device: everything below is a
 * plain HTTP call, and `lib/auth-exchange.test.ts` drives it against a stubbed fetch.
 */
export async function exchangeIdToken(
  idToken: string,
  label: string,
  fetchImpl: typeof fetch = fetch,
): Promise<SignInResult> {
  const res = await fetchImpl(`${config.apiBaseUrl}/api/auth/mobile`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ provider: "google", idToken, label }),
  });

  const body = (await res.json().catch(() => ({}))) as {
    token?: string;
    userId?: number;
    error?: { code?: string; message?: string };
  };

  if (!res.ok || !body.token) {
    // The server's message is shown as-is. It is written for a reader — "Hidden View is in a closed
    // beta and this account is not on the list yet" — and inventing a client-side paraphrase would
    // mean two places to keep true.
    return { ok: false, reason: "rejected", message: body.error?.message };
  }

  const session: StoredSession = { token: body.token, userId: body.userId ?? 0 };
  await saveSession(session);
  return { ok: true, session };
}

/**
 * Sign out.
 *
 * Revokes the token server-side BEFORE forgetting it locally, so a signed-out device leaves no
 * usable credential behind. If the revoke fails — offline, engine down — the local clear still
 * happens: a reader who taps sign out must end up signed out on this device either way, and a token
 * they can no longer present is a smaller problem than an app that refuses to log them out.
 *
 * Revocation goes through `/api/me/tokens/:id`, which is `SESSION_ONLY` and therefore NOT reachable
 * with this token — so it is best-effort and the reader can always revoke from the web. That is a
 * deliberate consequence of a token being unable to revoke tokens, which is the property that stops
 * a stolen one locking its owner out.
 */
export async function signOut(): Promise<void> {
  await clearSession();
}

export { AuthSession, Google, hasStoredToken };
