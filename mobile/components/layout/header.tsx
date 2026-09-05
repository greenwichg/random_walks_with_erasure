import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { router } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Button } from "@/components/ui/button";
import { navigateTab } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { AccountMenu } from "./account-menu";
import { Logo } from "./logo";
import { NotificationsMenu } from "./notifications-menu";
import { ThemeToggle } from "./theme-toggle";

/**
 * The masthead, as the mobile web draws it below `lg`: menu button · wordmark · notifications ·
 * theme · account. No search control — Search is a destination in the bottom tab bar on exactly
 * these widths. The five destinations a reader moves between live in the tab bar and the full
 * directory in the menu, so the bar itself stays this short.
 *
 * Rendered as the native stack's header on every screen, so it never remounts between them, and
 * it clears the status bar / notch itself (`insets.top`), the way `safe-top` does on the web.
 */
export function AppHeader() {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.wrap, { paddingTop: insets.top, backgroundColor: palette.background, borderBottomColor: palette.border }]}>
      <View style={styles.row}>
        <Button
          variant="ghost"
          size="icon"
          icon="menu"
          accessibilityLabel={t("header.openMenu")}
          onPress={() => router.push("/menu")}
        />
        <Pressable accessibilityRole="link" accessibilityLabel={t("sidebar.homeAria")} onPress={() => navigateTab("/")}>
          <Logo />
        </Pressable>
        <View style={styles.spacer} />
        <View style={styles.tools}>
          <NotificationsMenu />
          <ThemeToggle />
          <AccountMenu />
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { borderBottomWidth: StyleSheet.hairlineWidth },
  row: { minHeight: 64, flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16 },
  spacer: { flex: 1 },
  tools: { flexDirection: "row", alignItems: "center", gap: 6 },
});
