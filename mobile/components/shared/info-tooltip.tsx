import * as React from "react";
import { Pressable, StyleSheet, type StyleProp, type ViewStyle } from "react-native";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { radius, space } from "@/design/tokens";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * The small "i" affordance on every metric and section card. On the web a tap toggles a Radix
 * tooltip (touch never hovers); on a phone the same tap opens the text as a sheet, which is the
 * one overlay the platform dismisses the same way everywhere. The press never bubbles to the card
 * around it, so an "i" inside a link never navigates.
 */
export function InfoTooltip({ text, style }: { text: string; style?: StyleProp<ViewStyle> }) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t("common.moreInfo")}
        hitSlop={8}
        onPress={() => setOpen(true)}
        style={[styles.button, style]}
      >
        <Icon name="info" size={14} color={palette.mutedForeground} />
      </Pressable>
      <BottomSheet open={open} onClose={() => setOpen(false)} title={t("common.moreInfo")}>
        <Txt size={14} lineHeight={21} style={{ paddingHorizontal: space.sm, paddingBottom: space.md }}>
          {text}
        </Txt>
      </BottomSheet>
    </>
  );
}

const styles = StyleSheet.create({
  button: { width: 20, height: 20, borderRadius: radius.pill, alignItems: "center", justifyContent: "center", opacity: 0.7 },
});
