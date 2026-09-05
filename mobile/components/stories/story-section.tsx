import * as React from "react";
import { LayoutAnimation, Pressable, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { useTheme } from "@/lib/theme";

/**
 * A collapsible story section — the phone's whole story page is a stack of these.
 *
 * Collapsed, the six modules are one screen of TITLES: the page becomes a table of contents for
 * itself, and opening one is a decision rather than a scroll. THE DESCRIPTION IS THE POINT: the
 * line under each title says what it holds, and stays visible when open (it reads as a standfirst
 * there; a line that vanished on expand would flicker the whole stack).
 *
 * Edge-to-edge: the negative margin cancels the page gutter so each panel spans the full width like
 * the reference, while its own padding keeps the text on the page's measure. `bg-card` over the page
 * ground makes the gap between panels read as a divider without a rule to draw. The reveal animates
 * through `LayoutAnimation`, the platform's own height transition.
 */
export function StorySection({
  title,
  description,
  defaultOpen = false,
  children,
}: {
  title: string;
  description: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const { palette } = useTheme();
  const insets = useSafeAreaInsets();
  const [open, setOpen] = React.useState(defaultOpen);
  const gutter = { marginLeft: -Math.max(16, insets.left), marginRight: -Math.max(16, insets.right), paddingLeft: Math.max(16, insets.left), paddingRight: Math.max(16, insets.right) };

  const toggle = () => {
    LayoutAnimation.configureNext(LayoutAnimation.create(240, LayoutAnimation.Types.easeInEaseOut, LayoutAnimation.Properties.opacity));
    setOpen((v) => !v);
  };

  return (
    <View style={[styles.section, gutter, { backgroundColor: palette.card }]}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={toggle}
        style={styles.header}
      >
        <Txt display weight="700" size={26} lineHeight={30} tight accessibilityRole="header" style={{ flex: 1, minWidth: 0 }}>
          {title}
        </Txt>
        <Icon name={open ? "chevron-up" : "chevron-down"} size={24} color={palette.mutedForeground} />
      </Pressable>
      <Txt size={15} muted lineHeight={20} style={styles.description}>
        {description}
      </Txt>
      {open && <View style={styles.region}>{children}</View>}
    </View>
  );
}

/** The stack itself: `gap-2` over the page ground is the band between panels. */
export function StorySections({ children }: { children: React.ReactNode }) {
  return <View style={{ gap: 8 }}>{children}</View>;
}

const styles = StyleSheet.create({
  section: {},
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 16, paddingVertical: 20 },
  description: { marginTop: -8, paddingBottom: 20 },
  region: { paddingBottom: 24 },
});
