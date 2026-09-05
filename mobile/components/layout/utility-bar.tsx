import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * The slim utility strip under the masthead: today's date on the left, the browser-extension
 * entry point on the right. Deliberately narrow — the utilities a news masthead usually carries
 * have no backing feature, so they are omitted rather than rendered as dead controls.
 */
export function UtilityBar() {
  const { t, lang } = useTranslation();
  const { palette } = useTheme();
  const today = React.useMemo(
    () => new Date().toLocaleDateString(lang, { weekday: "long", year: "numeric", month: "long", day: "numeric" }),
    [lang],
  );
  return (
    <View style={[styles.row, { borderBottomColor: palette.border }]}>
      <Txt size={12} muted tabular numberOfLines={1} style={{ flexShrink: 1 }}>
        {today}
      </Txt>
      <Pressable accessibilityRole="link" onPress={() => navigate("/settings")} style={styles.link} hitSlop={6}>
        <Icon name="puzzle" size={14} color={palette.mutedForeground} />
        <Txt size={12} weight="500" muted>
          {t("home.utility.extension")}
        </Txt>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { height: 36, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 16, borderBottomWidth: StyleSheet.hairlineWidth },
  link: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 0 },
});
