import * as SecureStore from "expo-secure-store";

/**
 * Where the bearer token lives on a phone.
 *
 * `expo-secure-store` is the iOS Keychain and the Android Keystore — hardware-backed where the
 * device offers it, and readable only by this app. Not `AsyncStorage`: that is a plaintext file in
 * the app's sandbox, which is fine for a remembered tab and wrong for a credential that grants a
 * reader's whole account.
 *
 * The token is a Hidden View API token, the same kind the browser extension holds. It is minted by
 * `POST /api/auth/mobile` from a Google ID token and is the ONLY credential this app stores: the
 * Google token is used once, in that one request, and is never written down.
 */

const TOKEN_KEY = "ih.bearer.token";
const USER_KEY = "ih.bearer.userId";
const TOKEN_ID_KEY = "ih.bearer.tokenId";
/** The signed-in address, for the account menu. Not a credential; kept beside the token because
 *  it describes the token's owner and must be forgotten with it. */
const EMAIL_KEY = "ih.bearer.email";

/**
 * A synchronous cache of the token.
 *
 * `configureApi`'s `getToken` is called per request and must return synchronously, while the
 * keystore is async — so the token is read once at startup and kept here. That is not a weakening:
 * the process already holds the token in memory to send it, and the keystore's job is to protect it
 * at rest, between launches.
 */
let cached: string | null = null;

export interface StoredSession {
  token: string;
  userId: number;
  /** The engine's id for this token, so sign-out can revoke it rather than merely forget it. */
  tokenId?: number;
  /** The address the exchange verified. Absent on sessions written before it was stored. */
  email?: string | null;
}

/** Read the session off the keystore into the in-memory cache. Call once, before the first request. */
export async function loadSession(): Promise<StoredSession | null> {
  try {
    const [token, userId, email] = await Promise.all([
      SecureStore.getItemAsync(TOKEN_KEY),
      SecureStore.getItemAsync(USER_KEY),
      SecureStore.getItemAsync(EMAIL_KEY),
    ]);
    if (!token) {
      cached = null;
      return null;
    }
    cached = token;
    return { token, userId: Number(userId ?? 0), email: email ?? null };
  } catch {
    // A keystore that cannot be read (a device in an odd state, a restore from backup) is a signed
    // -out app, not a crashed one. The reader signs in again; nothing is lost but a session.
    cached = null;
    return null;
  }
}

export async function saveSession(session: StoredSession): Promise<void> {
  cached = session.token;
  await Promise.all([
    SecureStore.setItemAsync(TOKEN_KEY, session.token),
    SecureStore.setItemAsync(USER_KEY, String(session.userId)),
    session.tokenId != null
      ? SecureStore.setItemAsync(TOKEN_ID_KEY, String(session.tokenId))
      : Promise.resolve(),
    session.email
      ? SecureStore.setItemAsync(EMAIL_KEY, session.email)
      : SecureStore.deleteItemAsync(EMAIL_KEY),
  ]);
}

export async function clearSession(): Promise<void> {
  // The cache is cleared FIRST. If the keystore delete failed and this ran after it, an in-flight
  // request could still attach a token the reader believes they have signed out of.
  cached = null;
  await Promise.all([
    SecureStore.deleteItemAsync(TOKEN_KEY),
    SecureStore.deleteItemAsync(USER_KEY),
    SecureStore.deleteItemAsync(TOKEN_ID_KEY),
    SecureStore.deleteItemAsync(EMAIL_KEY),
  ]);
}

/** The token for the current request, or `null`. Synchronous by necessity — see {@link cached}. */
export function currentToken(): string | null {
  return cached;
}

/**
 * Whether the keystore holds a token — the ANSWER, never the token.
 *
 * For the account row, which has to make "the bearer token is stored securely" and "sign-out removed
 * it" observable on a device where there is no devtools panel. It reads the keystore rather than the
 * in-memory cache on purpose: the cache would say yes for a process that had simply not written
 * anything down, which is exactly the failure the check exists to rule out.
 */
export async function hasStoredToken(): Promise<boolean> {
  try {
    return (await SecureStore.getItemAsync(TOKEN_KEY)) !== null;
  } catch {
    return false;
  }
}
