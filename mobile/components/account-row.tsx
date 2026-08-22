import { useState } from "react";
import { Alert, Pressable, StyleSheet, Text, View } from "react-native";
import { useQueryClient } from "@tanstack/react-query";

import { config } from "@/lib/config";
import { hasStoredToken, signOut } from "@/lib/auth";
import { radius, space, type as typeScale, type Palette } from "@/design/tokens";

/**
 * The account row: what this build is pointed at, whether a credential is held, and sign-out.
 *
 * Not a feature — it is what makes three of the seven device-test items observable at all. On a
 * phone there is no devtools panel: "the token is stored securely" and "sign-out removes it" are
 * invisible without something on screen that says so, and a tester who cannot see them can only
 * report that the app "seems fine".
 *
 * **It reports the token as a boolean and never as a value.** The same rule the config verifier
 * follows, for the same reason: a screen that displays a credential is a screen that ends up in a
 * screenshot in a chat window. `hasStoredToken()` asks the keystore whether a key exists; nothing
 * reads the value out to display it.
 */
export function AccountRow({ palette }: { palette: Palette }) {
  const queryClient = useQueryClient();
  const [stored, setStored] = useState<boolean | null>(null);

  const check = async () => {
    setStored(await hasStoredToken());
  };

  const out = () => {
    Alert.alert("Sign out?", "This device will forget your Hidden View token.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Sign out",
        style: "destructive",
        onPress: () => {
          void signOut().then(async () => {
            // Everything cached belonged to the signed-out reader.
            queryClient.clear();
            setStored(await hasStoredToken());
          });
        },
      },
    ]);
  };

  return (
    <View style={[styles.row, { borderColor: palette.border, backgroundColor: palette.muted }]}>
      <View style={styles.facts}>
        <Text style={[typeScale.label, { color: palette.mutedForeground }]}>
          {config.buildProfile.toUpperCase()} · {hostOf(config.apiBaseUrl)}
        </Text>
        <Pressable onPress={check} accessibilityRole="button">
          <Text style={[typeScale.caption, { color: palette.mutedForeground }]}>
            {stored === null
              ? "Tap to check the keystore"
              : stored
                ? "Keystore: a token is stored"
                : "Keystore: no token stored"}
          </Text>
        </Pressable>
      </View>
      <Pressable onPress={out} accessibilityRole="button" style={styles.signOut}>
        <Text style={[typeScale.caption, { color: palette.right }]}>Sign out</Text>
      </Pressable>
    </View>
  );
}

/** Host only. The full URL adds nothing on a narrow screen and wraps badly. */
function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url || "not configured";
  }
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.md,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    marginBottom: space.lg,
  },
  facts: { gap: space.xs, flexShrink: 1 },
  signOut: { paddingHorizontal: space.sm, paddingVertical: space.xs },
});
