import * as React from "react";
import { Pressable, StyleSheet, TextInput, View } from "react-native";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { fontFamily } from "@/design/fonts";
import { alpha, radius } from "@/design/tokens";
import { matchesOption } from "@/lib/country-search";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

export interface FilterOption {
  value: string;
  /** Text, or a node (e.g. CountryBadge); `text` is its searchable name when the label is a node. */
  label: React.ReactNode;
  text?: string;
  count?: number;
}

/** Past this many options the sheet grows a search box — the publisher facet feeds thousands. */
const SEARCH_THRESHOLD = 15;

/**
 * A compact single-select filter: the trigger is the web's pill (`h-9 rounded-lg border`, tinted
 * when active, showing the current value), and the option list opens as a sheet with the same
 * reset row, count column and search-first behaviour past the threshold.
 */
export function FilterSelect({
  label,
  description,
  value,
  options,
  onChange,
  allLabel = "All",
  resettable = true,
}: {
  label: string;
  description?: string;
  value: string;
  options: FilterOption[];
  onChange: (v: string) => void;
  allLabel?: string;
  resettable?: boolean;
}) {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const active = value !== "all";
  const current = options.find((o) => o.value === value);
  const searchable = options.length > SEARCH_THRESHOLD;

  const shown = query ? options.filter((o) => matchesOption(o.text ?? (typeof o.label === "string" ? o.label : o.value), query)) : options;

  const pick = (v: string) => {
    onChange(v);
    setOpen(false);
  };

  const row = (v: string, labelNode: React.ReactNode, count?: number) => {
    const on = value === v;
    return (
      <Pressable
        key={v}
        accessibilityRole="radio"
        accessibilityState={{ checked: on }}
        onPress={() => pick(v)}
        style={({ pressed }) => [styles.row, pressed && { backgroundColor: palette.accent }]}
      >
        <Icon name="check" size={14} color={palette.primary} style={{ opacity: on ? 1 : 0 }} />
        {typeof labelNode === "string" ? (
          <Txt size={14} color={on ? palette.primary : palette.foreground} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
            {labelNode}
          </Txt>
        ) : (
          <View style={{ flex: 1, minWidth: 0 }}>{labelNode}</View>
        )}
        {count !== undefined && (
          <Txt size={12} muted tabular style={{ opacity: count === 0 ? 0.5 : 1 }}>
            {count}
          </Txt>
        )}
      </Pressable>
    );
  };

  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={() => {
          setQuery("");
          setOpen(true);
        }}
        style={({ pressed }) => [
          styles.trigger,
          {
            borderColor: active ? alpha(palette.primary, 0.3) : palette.border,
            backgroundColor: active ? alpha(palette.primary, 0.05) : pressed ? palette.accent : palette.card,
          },
        ]}
      >
        <Txt size={14} weight="500" color={active ? palette.primary : palette.foreground}>
          {label}
        </Txt>
        {active && current ? (
          typeof current.label === "string" ? (
            <Txt size={12} color={palette.primary} style={{ opacity: 0.8 }}>{`· ${current.label}`}</Txt>
          ) : (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
              <Txt size={12} color={palette.primary} style={{ opacity: 0.8 }}>·</Txt>
              {current.label}
            </View>
          )
        ) : null}
        <Icon name="chevron-down" size={14} color={active ? palette.primary : palette.foreground} style={{ opacity: 0.6 }} />
      </Pressable>

      <BottomSheet open={open} onClose={() => setOpen(false)} title={label} description={description}>
        {searchable && (
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder={t("filter.search")}
            placeholderTextColor={palette.mutedForeground}
            autoCapitalize="none"
            autoCorrect={false}
            style={[styles.search, { borderColor: palette.border, backgroundColor: palette.background, color: palette.foreground, fontFamily: fontFamily("400") }]}
          />
        )}
        {resettable && !query && row("all", allLabel)}
        {shown.map((o) => row(o.value, o.label, o.count))}
        {shown.length === 0 && (
          <Txt size={12} muted style={{ paddingHorizontal: 12, paddingVertical: 12 }}>
            {t("filter.noMatch", { q: query })}
          </Txt>
        )}
      </BottomSheet>
    </>
  );
}

const styles = StyleSheet.create({
  trigger: { flexDirection: "row", alignItems: "center", gap: 6, height: 36, borderWidth: 1, borderRadius: radius.lg, paddingHorizontal: 12 },
  row: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 8, paddingVertical: 8, borderRadius: radius.sm },
  search: { height: 36, borderWidth: 1, borderRadius: radius.md, paddingHorizontal: 12, fontSize: 13, marginHorizontal: 4, marginBottom: 8 },
});
