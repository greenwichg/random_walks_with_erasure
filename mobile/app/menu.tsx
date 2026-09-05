import { router } from "expo-router";
import * as React from "react";
import { ScrollView, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { MenuPanel } from "@/components/layout/menu-panel";
import { Button } from "@/components/ui/button";
import { Txt } from "@/components/ui/text";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * The mobile menu — the reference layout's full-screen directory: the panel fills the screen,
 * "Home" heads it, a close button sits opposite. The bottom tab bar carries the five destinations
 * a reader moves between, so the menu is the full directory rather than a second primary nav.
 */
export default function MenuScreen() {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const insets = useSafeAreaInsets();
  const close = React.useCallback(() => router.back(), []);

  return (
    <View style={{ flex: 1, backgroundColor: palette.background, paddingTop: insets.top }}>
      <View style={[styles.header, { borderBottomColor: palette.border }]}>
        <Txt size={15} weight="600" accessibilityRole="header">
          {t("nav.dashboard")}
        </Txt>
        <Button variant="ghost" size="icon" icon="x" accessibilityLabel={t("common.close")} onPress={close} />
      </View>
      <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom }}>
        <MenuPanel onNavigate={close} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  header: { height: 56, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 20, borderBottomWidth: StyleSheet.hairlineWidth },
});
