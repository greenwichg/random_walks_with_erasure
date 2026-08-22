import { useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import { useEffect, useState } from "react";
import { ActivityIndicator, Platform, Pressable, StyleSheet, Text, View, useColorScheme } from "react-native";

import { Google, exchangeIdToken, googleConfig, signInConfigured } from "@/lib/auth";
import { configProblems } from "@/lib/config";
import { dark, light, radius, space, type as typeScale } from "@/design/tokens";

/**
 * Sign in with Google.
 *
 * The flow, and where each half of the trust lives:
 *
 *   1. `Google.useIdTokenAuthRequest` opens the system browser (not a web view — Google rejects
 *      embedded web views for OAuth, and a system browser is what lets an already-signed-in Google
 *      account complete without typing a password).
 *   2. Google returns an **ID token** to the app.
 *   3. `exchangeIdToken` posts it to `/api/auth/mobile`, which verifies the signature against
 *      Google's published keys, checks it was minted for one of our client ids, requires a verified
 *      email, runs the closed-beta allowlist, and returns a Hidden View token.
 *
 * The app verifies nothing itself, on purpose: a client that decided whether its own token was
 * valid would be a client an attacker could patch. The Google ID token is never stored — only the
 * Hidden View token, and only in the keystore.
 */
export default function SignInScreen() {
  const scheme = useColorScheme();
  const palette = scheme === "dark" ? dark : light;
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const problems = configProblems();
  const configured = signInConfigured();

  const [request, response, promptAsync] = Google.useIdTokenAuthRequest(googleConfig());

  useEffect(() => {
    if (response?.type !== "success") {
      // "dismiss" and "cancel" are a reader changing their mind, not a failure. Showing an error for
      // them would blame somebody for closing a screen.
      if (response?.type === "error") setError("Google sign-in did not complete.");
      return;
    }
    const idToken = response.params?.id_token;
    if (!idToken) {
      // `openid` missing from the scopes is the usual cause, and it produces an access token with no
      // ID token — a response that looks successful and carries nothing the server can verify.
      setError("Google returned no ID token. Check that `openid` is in the requested scopes.");
      return;
    }
    setBusy(true);
    void exchangeIdToken(idToken, `${Platform.OS} app`)
      .then((result) => {
        if (result.ok) {
          // The token changed, so anything cached under the previous identity is somebody else's.
          void queryClient.invalidateQueries();
          router.replace("/");
        } else {
          setError(result.message ?? "Sign-in was refused.");
        }
      })
      .finally(() => setBusy(false));
  }, [response, queryClient]);

  return (
    <View style={[styles.screen, { backgroundColor: palette.background }]}>
      <Text style={[typeScale.title, { color: palette.foreground }]}>Sign in to Hidden View</Text>
      <Text style={[typeScale.body, styles.blurb, { color: palette.mutedForeground }]}>
        Hidden View reads your news diet, not your mind. Signing in links this device to your
        account so your recommendations are yours.
      </Text>

      {problems.length > 0 ? (
        <View style={[styles.notice, { borderColor: palette.caution }]}>
          <Text style={[typeScale.label, { color: palette.caution }]}>NOT CONFIGURED</Text>
          {problems.map((p) => (
            <Text key={p} style={[typeScale.caption, { color: palette.mutedForeground }]}>
              {p}
            </Text>
          ))}
        </View>
      ) : null}

      {busy ? (
        <ActivityIndicator color={palette.primary} />
      ) : (
        <Pressable
          accessibilityRole="button"
          disabled={!configured || !request}
          onPress={() => {
            setError(null);
            void promptAsync();
          }}
          style={[
            styles.cta,
            { backgroundColor: configured && request ? palette.primary : palette.muted },
          ]}
        >
          <Text
            style={[
              typeScale.headline,
              { color: configured && request ? palette.primaryForeground : palette.mutedForeground },
            ]}
          >
            Continue with Google
          </Text>
        </Pressable>
      )}

      {error ? (
        <Text style={[typeScale.caption, styles.error, { color: palette.right }]}>{error}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, alignItems: "center", justifyContent: "center", padding: space.xl, gap: space.lg },
  blurb: { textAlign: "center" },
  cta: { paddingHorizontal: space.xl, paddingVertical: space.md, borderRadius: radius.md },
  notice: {
    alignSelf: "stretch",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.md,
    padding: space.md,
    gap: space.xs,
  },
  error: { textAlign: "center" },
});
