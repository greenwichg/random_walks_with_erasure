import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { Txt } from "@/components/ui/text";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { Logo } from "./logo";

/**
 * The site footer — a secondary navigation surface at the end of a long editorial page. Every link
 * points at a route the product has; columns a masthead usually carries (Careers / Press / Apps)
 * are omitted rather than rendered as dead links.
 */
const COLUMNS: { titleKey: string; links: { href: string; labelKey: string }[] }[] = [
  {
    titleKey: "home.footer.product",
    links: [
      { href: "/", labelKey: "nav.dashboard" },
      { href: "/report", labelKey: "nav.report" },
      { href: "/recommendations", labelKey: "nav.recommendations" },
      { href: "/coach", labelKey: "nav.coach" },
    ],
  },
  {
    titleKey: "nav.section.explore",
    links: [
      { href: "/stories", labelKey: "nav.stories" },
      { href: "/discover", labelKey: "nav.discover" },
      { href: "/saved", labelKey: "nav.saved" },
      { href: "/history", labelKey: "nav.history" },
      { href: "/analytics", labelKey: "nav.analytics" },
    ],
  },
  {
    titleKey: "nav.section.account",
    links: [
      { href: "/profile", labelKey: "nav.profile" },
      { href: "/settings", labelKey: "nav.settings" },
      { href: "/analyze", labelKey: "home.footer.analyze" },
      { href: "/privacy", labelKey: "home.footer.privacy" },
    ],
  },
];

export function SiteFooter() {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const year = new Date().getFullYear();

  return (
    <View style={[styles.footer, { borderTopColor: palette.border }]}>
      <View style={styles.grid}>
        <View style={styles.brand}>
          <Logo />
          <Txt size={12} muted lineHeight={18} style={{ marginTop: 12, maxWidth: 220 }}>
            {t("home.footer.tagline")}
          </Txt>
        </View>
        {COLUMNS.map((col) => (
          <View key={col.titleKey} style={styles.column} accessibilityRole="menu" accessibilityLabel={t(col.titleKey)}>
            <Txt size={11} weight="600" uppercase tracking={0.6} muted style={{ marginBottom: 10, opacity: 0.7 }}>
              {t(col.titleKey)}
            </Txt>
            <View style={{ gap: 6 }}>
              {col.links.map((link) => (
                <Pressable key={link.href} accessibilityRole="link" onPress={() => navigate(link.href)} hitSlop={4}>
                  <Txt size={12} muted>
                    {t(link.labelKey)}
                  </Txt>
                </Pressable>
              ))}
            </View>
          </View>
        ))}
      </View>
      <Txt size={12} muted style={[styles.rights, { borderTopColor: palette.border }]}>
        {t("home.footer.rights", { year })}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  footer: { marginTop: 56, borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 40 },
  grid: { flexDirection: "row", flexWrap: "wrap", rowGap: 32, columnGap: 32 },
  brand: { width: "100%" },
  column: { width: "40%" },
  rights: { marginTop: 36, borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 20 },
});
