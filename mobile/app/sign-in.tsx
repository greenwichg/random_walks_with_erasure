import * as React from "react";
import { ActivityIndicator, Platform, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Logo } from "@/components/layout/logo";
import { Button } from "@/components/ui/button";
import { Txt } from "@/components/ui/text";
import { radius, space } from "@/design/tokens";
import { Google, exchangeIdToken, googleConfig, signInConfigured } from "@/lib/auth";
import { useAuth } from "@/lib/auth-context";
import { configProblems } from "@/lib/config";
import { useTheme } from "@/lib/theme";

/**
 * Sign in with Google — the web's `/signin`, natively.
 *
 *   1. `Google.useIdTokenAuthRequest` opens the system browser (Google rejects embedded web views
 *      for OAuth, and a system browser lets an already-signed-in account complete without typing).
 *   2. Google returns an **ID token** to the app.
 *   3. `exchangeIdToken` posts it to `/api/auth/mobile`, which verifies the signature against
 *      Google's published keys, checks the audience is one of ours, requires a verified email, runs
 *      the closed-beta allowlist, and returns a Hidden View token — into the platform keystore.
 *
 * The app verifies nothing itself, on purpose. Android and iOS each present their own OAuth
 * client id (`googleConfig`), and the server trusts each independently.
 */
export default function SignInScreen() {
  const { palette } = useTheme();
  const insets = useSafeAreaInsets();
  const { setSession } = useAuth();
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const problems = configProblems();
  const configured = signInConfigured();
  const [request, response, promptAsync] = Google.useIdTokenAuthRequest(googleConfig());

  React.useEffect(() => {
    if (response?.type !== "success") {
      // "dismiss" and "cancel" are a reader changing their mind, not a failure.
      if (response?.type === "error") setError("Google sign-in did not complete.");
      return;
    }
    const idToken = response.params?.id_token;
    if (!idToken) {
      setError("Google returned no ID token. Check that `openid` is in the requested scopes.");
      return;
    }
    setBusy(true);
    void exchangeIdToken(idToken, `${Platform.OS} app`)
      .then((result) => {
        if (result.ok) setSession(result.session);
        else setError(result.message ?? "Sign-in was refused.");
      })
      .finally(() => setBusy(false));
  }, [response, setSession]);

  return (
    <View style={[styles.screen, { backgroundColor: palette.background, paddingTop: insets.top + space.xl, paddingBottom: insets.bottom + space.xl }]}>
      <Logo />
      <Txt display weight="700" size={24} lineHeight={30} tight align="center" style={{ marginTop: space.xl }}>
        Sign in to Hidden View
      </Txt>
      <Txt size={15} muted align="center" lineHeight={22} style={{ marginTop: space.sm, maxWidth: 360 }}>
        Hidden View reads your news diet, not your mind. Signing in links this device to your account so
        your recommendations are yours.
      </Txt>

      {problems.length > 0 ? (
        <View style={[styles.notice, { borderColor: palette.caution }]}>
          <Txt size={11} weight="600" color={palette.caution} tracking={0.4}>
            NOT CONFIGURED
          </Txt>
          {problems.map((p) => (
            <Txt key={p} size={13} muted>
              {p}
            </Txt>
          ))}
        </View>
      ) : null}

      <View style={{ marginTop: space.xl, alignSelf: "stretch", alignItems: "center" }}>
        {busy ? (
          <ActivityIndicator color={palette.primary} />
        ) : (
          <Button
            size="lg"
            disabled={!configured || !request}
            onPress={() => {
              setError(null);
              void promptAsync();
            }}
          >
            Continue with Google
          </Button>
        )}
      </View>

      {error ? (
        <Txt size={13} color={palette.negative} align="center" style={{ marginTop: space.lg }}>
          {error}
        </Txt>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: space.xl },
  notice: {
    alignSelf: "stretch",
    marginTop: space.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.md,
    padding: space.md,
    gap: space.xs,
  },
});
