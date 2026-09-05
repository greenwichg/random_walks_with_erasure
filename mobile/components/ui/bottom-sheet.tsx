import * as React from "react";
import { Modal, Pressable, ScrollView, StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { radius, space } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

import { Icon } from "./icon";
import { Txt } from "./text";

/**
 * The one sheet.
 *
 * The web has three overlays that are, on a phone, the same gesture: the Radix `Sheet` (the bias
 * card's +N list, the mobile menu), the Radix `DropdownMenu` (account, notifications, the card
 * menus, the filter pickers) and the two searchable popovers (countries, long filter lists). Every
 * one of them is a panel that slides up from the bottom, dims the page, and closes on a tap
 * outside — so they all render through this. `title` and `description` are the sheet's own header
 * (`SheetTitle` / `SheetDescription`); `children` scroll inside a height bounded by the screen.
 */
export function BottomSheet({
  open,
  onClose,
  title,
  description,
  children,
  maxHeight = "80%",
  scroll = true,
  contentStyle,
}: {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  description?: string;
  children: React.ReactNode;
  maxHeight?: ViewStyle["maxHeight"];
  /** Wrap the body in a ScrollView. Off for bodies that scroll themselves (a FlatList). */
  scroll?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
}) {
  const { palette } = useTheme();
  const insets = useSafeAreaInsets();

  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent>
      <View style={styles.root}>
        <Pressable accessibilityLabel="Close" style={styles.backdrop} onPress={onClose} />
        <View
          style={[
            styles.sheet,
            { backgroundColor: palette.popover, borderColor: palette.border, maxHeight, paddingBottom: Math.max(insets.bottom, space.lg) },
          ]}
        >
          <View style={[styles.grabber, { backgroundColor: palette.border }]} />
          {(title || description) && (
            <View style={styles.header}>
              <View style={{ flex: 1, minWidth: 0 }}>
                {typeof title === "string" ? (
                  <Txt size={14} weight="600">
                    {title}
                  </Txt>
                ) : (
                  title
                )}
                {description && (
                  <Txt size={12} muted style={{ marginTop: 2 }}>
                    {description}
                  </Txt>
                )}
              </View>
              <Pressable accessibilityRole="button" accessibilityLabel="Close" onPress={onClose} hitSlop={8} style={styles.close}>
                <Icon name="x" size={18} color={palette.mutedForeground} />
              </Pressable>
            </View>
          )}
          {scroll ? (
            <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={[styles.body, contentStyle]}>
              {children}
            </ScrollView>
          ) : (
            <View style={[styles.body, { flexShrink: 1 }, contentStyle]}>{children}</View>
          )}
        </View>
      </View>
    </Modal>
  );
}

/** One row of a sheet used as a MENU (`DropdownMenuItem`). */
export function SheetItem({
  label,
  icon,
  onPress,
  destructive = false,
  trailing,
  disabled = false,
}: {
  label: string;
  icon?: React.ComponentProps<typeof Icon>["name"];
  onPress: () => void;
  destructive?: boolean;
  trailing?: React.ReactNode;
  disabled?: boolean;
}) {
  const { palette } = useTheme();
  const fg = destructive ? palette.destructive : palette.foreground;
  return (
    <Pressable
      accessibilityRole="menuitem"
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.item, pressed && { backgroundColor: palette.accent }, disabled && { opacity: 0.5 }]}
    >
      {icon && <Icon name={icon} size={16} color={fg} />}
      <Txt size={14} color={fg} style={{ flex: 1 }}>
        {label}
      </Txt>
      {trailing}
    </Pressable>
  );
}

export function SheetSeparator() {
  const { palette } = useTheme();
  return <View style={[styles.separator, { backgroundColor: palette.border }]} />;
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.5)" },
  sheet: {
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
    borderWidth: StyleSheet.hairlineWidth,
    paddingTop: space.sm,
  },
  grabber: { alignSelf: "center", width: 36, height: 4, borderRadius: radius.pill, marginBottom: space.sm },
  header: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: space.md,
    paddingHorizontal: space.lg,
    paddingTop: space.sm,
    paddingBottom: space.xs,
  },
  close: { padding: 4, marginTop: -2 },
  body: { paddingHorizontal: space.sm, paddingTop: space.xs },
  item: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    paddingHorizontal: space.md,
    paddingVertical: 10,
    borderRadius: radius.sm,
  },
  separator: { height: StyleSheet.hairlineWidth, marginVertical: 4, marginHorizontal: -space.sm },
});
