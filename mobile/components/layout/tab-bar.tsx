import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useGlobalSearchParams, usePathname } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Icon, type IconName } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { navigateTab } from "@/lib/navigation";
import { useLocalHref } from "@/lib/use-local-href";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/** The bar's own height, without the home indicator. Screens reserve it (plus the inset). */
export const TAB_BAR_HEIGHT = 56;

/**
 * The bottom tab bar — the reference layout's five destinations: Home · For You · Search ·
 * Blind spots · Local, the same five the desktop masthead names, in the same order. Fixed on
 * every screen (as the web's is), opaque (a translucent bar let rows read through the labels),
 * sitting above the home indicator, every target clearing 44px.
 *
 * Active state is read from the current route exactly as the web reads it from the URL: Blind
 * spots and Local are both the Stories browser, told apart by which query param is set.
 */
export function TabBar() {
  const pathname = usePathname();
  const params = useGlobalSearchParams<{ blindspot?: string; country?: string }>();
  const { t } = useTranslation();
  const { palette } = useTheme();
  const insets = useSafeAreaInsets();
  const localHref = useLocalHref();

  const onStories = pathname === "/stories";
  const items: { href: string; label: string; icon: IconName; active: boolean }[] = [
    { href: "/", label: t("nav.dashboard"), icon: "newspaper", active: pathname === "/" },
    { href: "/recommendations", label: t("nav.forYou"), icon: "sparkles", active: pathname.startsWith("/recommendations") },
    { href: "/search", label: t("header.search"), icon: "search", active: pathname.startsWith("/search") },
    { href: "/stories?blindspot=any", label: t("home.blindspots.title"), icon: "eye-off", active: onStories && !!params.blindspot },
    { href: localHref, label: t("nav.local"), icon: "map-pin", active: onStories && !!params.country },
  ];

  return (
    <View
      accessibilityRole="tablist"
      style={[styles.bar, { backgroundColor: palette.card, borderTopColor: palette.border, paddingBottom: insets.bottom }]}
    >
      {items.map((item) => (
        <Pressable
          key={item.label}
          accessibilityRole="tab"
          accessibilityState={{ selected: item.active }}
          onPress={() => navigateTab(item.href)}
          style={styles.item}
        >
          <Icon name={item.icon} size={20} color={item.active ? palette.primary : palette.mutedForeground} />
          <Txt size={10} weight="500" color={item.active ? palette.foreground : palette.mutedForeground} numberOfLines={1} lineHeight={12}>
            {item.label}
          </Txt>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    flexDirection: "row",
    alignItems: "stretch",
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  item: { flex: 1, height: TAB_BAR_HEIGHT, alignItems: "center", justifyContent: "center", gap: 4, paddingHorizontal: 4 },
});
