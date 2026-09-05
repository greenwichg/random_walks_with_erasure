import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { navigate } from "@/lib/navigation";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

export interface TopicEntry {
  value: string;
  label: string;
  count?: number;
  href?: string;
}

/**
 * The "Related Topics" index — one design, two jobs: a row is a link (`href`) or a filter the
 * host applies to itself (`onSelect`). Hairline-separated rows, the label truncated as the LAST
 * line of defence (names are bounded engine-side), a count on the right when there is one.
 */
export function TopicList({
  items,
  active,
  onSelect,
}: {
  items: TopicEntry[];
  active?: string | null;
  onSelect?: (value: string | null) => void;
}) {
  const { formatCompact } = useTranslation();
  const { palette } = useTheme();
  if (items.length === 0) return null;
  return (
    <View style={{ marginTop: 8 }}>
      {items.map((entry, i) => {
        const on = active === entry.value;
        return (
          <Pressable
            key={entry.value}
            accessibilityRole={entry.href ? "link" : "button"}
            accessibilityState={entry.href ? undefined : { selected: on }}
            onPress={() => (entry.href ? navigate(entry.href) : onSelect?.(on ? null : entry.value))}
            style={({ pressed }) => [
              styles.row,
              { borderBottomColor: palette.border, borderBottomWidth: i === items.length - 1 ? 0 : StyleSheet.hairlineWidth },
              pressed && { opacity: 0.7 },
            ]}
          >
            <Txt size={14} weight="500" numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
              {entry.label}
            </Txt>
            <View style={styles.trailing}>
              {entry.count === undefined ? null : (
                <Txt size={11} muted tabular>
                  {formatCompact(entry.count)}
                </Txt>
              )}
              <Icon name={on ? "check" : "plus"} size={16} color={on ? palette.foreground : palette.mutedForeground} />
            </View>
          </Pressable>
        );
      })}
    </View>
  );
}

/** Reveal control for a list longer than its initial window. */
export function ShowAllButton({ onPress, label }: { onPress: () => void; label: string }) {
  const { palette } = useTheme();
  return (
    <View style={styles.center}>
      <Pressable
        accessibilityRole="button"
        onPress={onPress}
        style={({ pressed }) => [styles.showAll, { borderColor: palette.border, backgroundColor: pressed ? palette.accent : "transparent" }]}
      >
        <Txt size={13} weight="500">
          {label}
        </Txt>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, paddingVertical: 10 },
  trailing: { flexDirection: "row", alignItems: "center", gap: 8 },
  center: { alignItems: "center", marginTop: 12 },
  showAll: { borderWidth: 1, borderRadius: radius.md, paddingHorizontal: 16, paddingVertical: 6 },
});
