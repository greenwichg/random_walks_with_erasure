import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";

import { clearSession, loadSession, type StoredSession } from "./session.ts";

/**
 * Who is signed in, for the tree.
 *
 * The web reads `useSession()` from NextAuth; every gated query (`useNotifications`, the feedback
 * ledger) and the header's account menu key off it. This is that hook's native counterpart, over
 * the keystore session: `ready` is false until the keystore has been read (rendering before that
 * would fire one unauthenticated request and show a signed-out screen to a signed-in reader),
 * `session` is null when signed out, and the two mutators are the only ways the answer changes.
 */
interface AuthValue {
  ready: boolean;
  session: StoredSession | null;
  signedIn: boolean;
  /** Called by the sign-in screen after `exchangeIdToken` has written the keystore. */
  setSession: (session: StoredSession) => void;
  signOut: () => Promise<void>;
}

const AuthContext = React.createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [ready, setReady] = React.useState(false);
  const [session, setSessionState] = React.useState<StoredSession | null>(null);

  React.useEffect(() => {
    // Never rejects — `loadSession` turns an unreadable keystore into "signed out" rather than a
    // crash, so there is no failure branch to handle here.
    void loadSession()
      .then((s) => setSessionState(s))
      .finally(() => setReady(true));
  }, []);

  const setSession = React.useCallback(
    (s: StoredSession) => {
      setSessionState(s);
      // The token changed, so anything cached under the previous identity is somebody else's.
      void queryClient.invalidateQueries();
    },
    [queryClient],
  );

  const signOut = React.useCallback(async () => {
    // Local clear happens whatever else fails: a reader who taps sign out must end up signed out
    // on this device. Server-side revocation is not reachable with this token (SESSION_ONLY) —
    // see lib/auth.ts.
    await clearSession();
    setSessionState(null);
    // Everything cached belonged to the signed-out reader.
    queryClient.clear();
  }, [queryClient]);

  const value = React.useMemo<AuthValue>(
    () => ({ ready, session, signedIn: session !== null, setSession, signOut }),
    [ready, session, setSession, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) {
    return { ready: true, session: null, signedIn: false, setSession: () => {}, signOut: async () => {} };
  }
  return ctx;
}
