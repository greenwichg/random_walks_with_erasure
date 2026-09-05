import * as React from "react";
import { Pressable, ScrollView, StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import { radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

import { Icon, type IconName } from "./icon";
import { Txt } from "./text";

export interface TabItem<V extends string> {
  value: V;
  label: string;
  icon?: IconName;
}

/**
 * `ui/tabs.tsx`: the recessed `bg-muted p-1 rounded-lg` list with a lifted `bg-card` tile for the
 * selected segment. The list scrolls sideways rather than squashing labels when it overflows a
 * narrow screen (the web's MB1 rule), which is why each trigger keeps its natural width.
 */
export function Tabs<V extends string>({
  value,
  onChange,
  items,
  style,
  full = false,
}: {
  value: V;
  onChange: (v: V) => void;
  items: TabItem<V>[];
  style?: StyleProp<ViewStyle>;
  /** `w-full justify-start`: the list spans the row. */
  full?: boolean;
}) {
  const { palette } = useTheme();
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={[full ? styles.fullScroll : styles.scroll, style]}
      contentContainerStyle={[styles.list, { backgroundColor: palette.muted }, full && styles.fullList]}
      accessibilityRole="tablist"
    >
      {items.map((item) => {
        const active = item.value === value;
        return (
          <Pressable
            key={item.value}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            onPress={() => onChange(item.value)}
            style={[
              styles.trigger,
              active && { backgroundColor: palette.card, ...shadow },
            ]}
          >
            {item.icon && (
              <Icon name={item.icon} size={14} color={active ? palette.foreground : palette.mutedForeground} />
            )}
            <Txt size={14} weight="500" color={active ? palette.foreground : palette.mutedForeground}>
              {item.label}
            </Txt>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

/** `shadow-soft`. */
const shadow: ViewStyle = {
  shadowColor: "#000",
  shadowOpacity: 0.06,
  shadowRadius: 2,
  shadowOffset: { width: 0, height: 1 },
  elevation: 1,
};

const styles = StyleSheet.create({
  scroll: { flexGrow: 0, alignSelf: "flex-start", maxWidth: "100%" },
  fullScroll: { flexGrow: 0, alignSelf: "stretch" },
  list: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    height: 36,
    padding: 4,
    borderRadius: radius.lg,
  },
  fullList: { minWidth: "100%" },
  trigger: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: radius.md,
  },
});

export function TabsSpacer() {
  return <View style={{ height: 12 }} />;
}
