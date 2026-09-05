import * as React from "react";
import { Alert, Pressable, StyleSheet, View } from "react-native";

import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { useAuth } from "@/lib/auth-context";
import { useDiscover } from "@/lib/hooks";
import { navigate } from "@/lib/navigation";
import { useLocalHref } from "@/lib/use-local-href";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/** How many catalog topics the menu lists before "Discover more topics" takes over. */
const TOPIC_LIMIT = 12;

function Row({
  href,
  onPress,
  children,
  chevron = true,
  onNavigate,
}: {
  href?: string;
  onPress?: () => void;
  children: string;
  chevron?: boolean;
  onNavigate?: () => void;
}) {
  const { palette } = useTheme();
  return (
    <Pressable
      accessibilityRole={href ? "link" : "button"}
      onPress={() => {
        if (href) {
          onNavigate?.();
          navigate(href);
        } else onPress?.();
      }}
      style={({ pressed }) => [styles.row, pressed && { backgroundColor: palette.accent }]}
    >
      <Txt size={15} lineHeight={20} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
        {children}
      </Txt>
      {chevron && <Icon name="chevron-right" size={16} color={palette.mutedForeground} />}
    </Pressable>
  );
}

function Divider() {
  const { palette } = useTheme();
  return <View style={[styles.divider, { borderTopColor: palette.border }]} />;
}

/**
 * THE menu body — the reference layout's directory panel: account rows, the reader's own
 * surfaces, tools, the catalog's topics, the records, privacy. Every row is a real Hidden View
 * route (the ones this app has no screen for open the web page — see lib/navigation.ts), and the
 * topics come from the live catalog, never a hardcoded desk list.
 */
export function MenuPanel({ onNavigate }: { onNavigate: () => void }) {
  const { t } = useTranslation();
  const { signOut } = useAuth();
  const facets = useDiscover({});
  const localHref = useLocalHref();
  const topics = (facets.data?.topics ?? []).slice(0, TOPIC_LIMIT);

  const out = () =>
    Alert.alert(t("header.signOut"), "This device will forget your Hidden View token.", [
      { text: t("common.close"), style: "cancel" },
      { text: t("header.signOut"), style: "destructive", onPress: () => { onNavigate(); void signOut(); } },
    ]);

  return (
    <View style={styles.list} accessibilityRole="menu" accessibilityLabel={t("header.primaryNav")}>
      <Row href="/" chevron={false} onNavigate={onNavigate}>{t("nav.dashboard")}</Row>
      <Row href="/profile" chevron={false} onNavigate={onNavigate}>{t("home.menu.myAccount")}</Row>
      <Row href="/report" chevron={false} onNavigate={onNavigate}>{t("home.myBias.title")}</Row>
      <Row href="/alerts" chevron={false} onNavigate={onNavigate}>{t("alerts.title")}</Row>
      <Row chevron={false} onPress={out}>{t("header.signOut")}</Row>
      <Divider />
      <Row href="/recommendations" onNavigate={onNavigate}>{t("nav.forYou")}</Row>
      <Row href="/coach" onNavigate={onNavigate}>{t("nav.coach")}</Row>
      <Row href="/analytics" onNavigate={onNavigate}>{t("nav.analytics")}</Row>
      <Divider />
      <Row href="/settings" onNavigate={onNavigate}>{t("nav.settings")}</Row>
      <Row href="/analyze" onNavigate={onNavigate}>{t("home.footer.analyze")}</Row>
      <Row href="/settings" onNavigate={onNavigate}>{t("home.utility.extension")}</Row>
      <Divider />
      <Txt size={11} weight="600" uppercase tracking={0.6} muted style={styles.kicker}>
        {t("home.menu.topics")}
      </Txt>
      {topics.map((topic) => (
        <Row key={topic} href={`/stories?topic=${encodeURIComponent(topic)}`} onNavigate={onNavigate}>
          {topic}
        </Row>
      ))}
      <Row href={localHref} onNavigate={onNavigate}>{t("nav.local")}</Row>
      <Row href="/stories?blindspot=any" onNavigate={onNavigate}>{t("home.blindspots.title")}</Row>
      <Row href="/topics" onNavigate={onNavigate}>{t("home.menu.discoverMore")}</Row>
      <Divider />
      <Row href="/stories" onNavigate={onNavigate}>{t("nav.stories")}</Row>
      <Row href="/saved" onNavigate={onNavigate}>{t("nav.saved")}</Row>
      <Row href="/history" onNavigate={onNavigate}>{t("nav.history")}</Row>
      <Divider />
      <Row href="/privacy" chevron={false} onNavigate={onNavigate}>{t("home.footer.privacy")}</Row>
    </View>
  );
}

const styles = StyleSheet.create({
  list: { paddingTop: 8, paddingBottom: 32 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, paddingHorizontal: 20, paddingVertical: 12, minHeight: 44 },
  divider: { borderTopWidth: StyleSheet.hairlineWidth, marginVertical: 6 },
  kicker: { paddingHorizontal: 20, paddingTop: 6, paddingBottom: 4 },
});
