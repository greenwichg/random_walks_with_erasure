import * as React from "react";
import { Alert, Pressable, StyleSheet, View } from "react-native";

import { BottomSheet, SheetItem, SheetSeparator } from "@/components/ui/bottom-sheet";
import { Txt } from "@/components/ui/text";
import { radius, space } from "@/design/tokens";
import { useAuth } from "@/lib/auth-context";
import { config } from "@/lib/config";
import { navigate } from "@/lib/navigation";
import { hasStoredToken } from "@/lib/session";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * The avatar and the account menu behind it: the reader's own surfaces (Report · Saved · History ·
 * Profile · Settings) and Sign out — every row the web's menu has, in its order.
 *
 * `/api/profile` is session-only, so the menu names the reader by the address the sign-in
 * exchange verified rather than by a display name it cannot fetch. The diagnostics line (build ·
 * host · keystore) is what keeps "the token is stored securely" and "sign-out removed it"
 * observable on a device with no devtools panel; it reports the token as a boolean, never a value.
 */
export function AccountMenu() {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const { session, signOut } = useAuth();
  const [open, setOpen] = React.useState(false);
  const [stored, setStored] = React.useState<boolean | null>(null);

  const email = session?.email ?? "";
  const initials = (email.split("@")[0] ?? "").slice(0, 2).toUpperCase() || "U";

  const go = (href: string) => {
    setOpen(false);
    navigate(href);
  };

  const out = () => {
    Alert.alert(t("header.signOut"), "This device will forget your Hidden View token.", [
      { text: t("common.close"), style: "cancel" },
      {
        text: t("header.signOut"),
        style: "destructive",
        onPress: () => {
          setOpen(false);
          void signOut();
        },
      },
    ]);
  };

  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={email || t("home.menu.myAccount")}
        onPress={() => setOpen(true)}
        style={({ pressed }) => [styles.avatar, { backgroundColor: palette.muted, opacity: pressed ? 0.8 : 1 }]}
      >
        <Txt size={13} weight="600" muted>
          {initials}
        </Txt>
      </Pressable>

      <BottomSheet open={open} onClose={() => setOpen(false)}>
        <View style={styles.label}>
          <Txt size={14} weight="500">
            {t("home.menu.myAccount")}
          </Txt>
          {email ? (
            <Txt size={12} muted>
              {email}
            </Txt>
          ) : null}
        </View>
        <SheetSeparator />
        <SheetItem label={t("nav.report")} onPress={() => go("/report")} />
        <SheetItem label={t("nav.saved")} onPress={() => go("/saved")} />
        <SheetItem label={t("nav.history")} onPress={() => go("/history")} />
        <SheetSeparator />
        <SheetItem label={t("nav.profile")} onPress={() => go("/profile")} />
        <SheetItem label={t("nav.settings")} onPress={() => go("/settings")} />
        <SheetSeparator />
        <SheetItem label={t("header.signOut")} destructive onPress={out} />
        <SheetSeparator />
        <Pressable
          accessibilityRole="button"
          onPress={() => void hasStoredToken().then(setStored)}
          style={styles.diagnostics}
        >
          <Txt size={11} weight="600" muted tracking={0.4} uppercase>
            {config.buildProfile} · {hostOf(config.apiBaseUrl)}
          </Txt>
          <Txt size={12} muted style={{ marginTop: 2 }}>
            {stored === null ? "Tap to check the keystore" : stored ? "Keystore: a token is stored" : "Keystore: no token stored"}
          </Txt>
        </Pressable>
      </BottomSheet>
    </>
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
  avatar: { width: 36, height: 36, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  label: { paddingHorizontal: space.md, paddingVertical: space.sm },
  diagnostics: { paddingHorizontal: space.md, paddingVertical: space.sm },
});
