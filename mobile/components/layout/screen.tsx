import * as React from "react";
import { ScrollView, StyleSheet, View, type RefreshControlProps, type StyleProp, type ViewStyle } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Txt } from "@/components/ui/text";
import { useTheme } from "@/lib/theme";

import { SiteFooter } from "./site-footer";
import { TAB_BAR_HEIGHT } from "./tab-bar";
import { TopicStrip } from "./topic-strip";
import { UtilityBar } from "./utility-bar";

/**
 * The app shell's scrolling body, as `app/(app)/layout.tsx` + `PageContainer` compose it on the
 * web: the topic strip and the utility bar under the masthead, the page on the centred column
 * (16px gutters, widened by the safe-area insets on a notched device in landscape), the footer,
 * and the spacer that keeps the last row out from under the fixed tab bar.
 */
export function Screen({
  children,
  chrome = true,
  pt = 24,
  contentStyle,
  refreshControl,
}: {
  children: React.ReactNode;
  chrome?: boolean;
  /** `PageContainer`'s top padding: 24 by default, 16 on Home (`pt-4`). */
  pt?: number;
  contentStyle?: StyleProp<ViewStyle>;
  refreshControl?: React.ReactElement<RefreshControlProps>;
}) {
  const { palette } = useTheme();
  const insets = useSafeAreaInsets();
  const gutters = { paddingLeft: Math.max(16, insets.left), paddingRight: Math.max(16, insets.right) };

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: palette.background }}
      contentContainerStyle={{ paddingBottom: TAB_BAR_HEIGHT + insets.bottom }}
      keyboardShouldPersistTaps="handled"
      refreshControl={refreshControl}
    >
      {chrome && <TopicStrip />}
      {chrome && (
        <View style={gutters}>
          <UtilityBar />
        </View>
      )}
      <View style={[gutters, { paddingTop: pt, paddingBottom: 24 }, contentStyle]}>{children}</View>
      {chrome && (
        <View style={[gutters, { paddingBottom: Math.max(24, insets.bottom) }]}>
          <SiteFooter />
        </View>
      )}
    </ScrollView>
  );
}

/** The page's own heading block: `text-2xl font-semibold tracking-tight` + a muted subtitle. */
export function PageTitle({ title, subtitle, action }: { title: string; subtitle?: string; action?: React.ReactNode }) {
  return (
    <View style={styles.titleRow}>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Txt display weight="600" size={24} lineHeight={30} tight accessibilityRole="header">
          {title}
        </Txt>
        {subtitle && (
          <Txt size={14} muted style={{ marginTop: 4, maxWidth: 560 }}>
            {subtitle}
          </Txt>
        )}
      </View>
      {action}
    </View>
  );
}

const styles = StyleSheet.create({
  titleRow: { flexDirection: "row", alignItems: "flex-end", justifyContent: "space-between", gap: 12, marginBottom: 24 },
});
