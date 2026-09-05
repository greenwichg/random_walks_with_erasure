import * as React from "react";
import { Pressable, StyleSheet, TextInput, View } from "react-native";

import { countryName } from "@ih/core/logic/countries";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Icon } from "@/components/ui/icon";
import { Txt } from "@/components/ui/text";
import { fontFamily } from "@/design/fonts";
import { alpha, radius } from "@/design/tokens";
import { matchesCountry } from "@/lib/country-search";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { CountryBadge } from "./country-badge";

/**
 * The one searchable country picker: a dashed trigger chip that opens a search-first list over
 * the full country list. Selection stays the CALLER's state; `multi` keeps the sheet open across
 * toggles, single-select closes on pick.
 */
export function CountryPicker({
  options,
  isSelected,
  onToggle,
  triggerLabel,
  searchPlaceholder,
  noMatchLabel,
  multi = false,
  full = false,
  fullNote,
  dialogLabel,
}: {
  options: ReadonlyArray<{ country: string }>;
  isSelected: (code: string) => boolean;
  onToggle: (code: string) => void;
  triggerLabel: string;
  searchPlaceholder: string;
  noMatchLabel: (q: string) => string;
  multi?: boolean;
  full?: boolean;
  fullNote?: string;
  dialogLabel: string;
}) {
  const { lang } = useTranslation();
  const { palette } = useTheme();
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");

  const shown = React.useMemo(
    () => options.filter((c) => matchesCountry(c.country, countryName(c.country, lang), query)),
    [options, query, lang],
  );

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
          open
            ? { borderColor: alpha(palette.primary, 0.4), backgroundColor: alpha(palette.primary, 0.1) }
            : { borderColor: palette.border, backgroundColor: pressed ? palette.accent : "transparent" },
        ]}
      >
        <Txt size={12} weight="500" color={open ? palette.primary : palette.mutedForeground}>
          {triggerLabel}
        </Txt>
        <Icon name={open ? "chevron-up" : "chevron-down"} size={14} color={open ? palette.primary : palette.mutedForeground} />
      </Pressable>

      <BottomSheet open={open} onClose={() => setOpen(false)} title={dialogLabel}>
        <TextInput
          value={query}
          onChangeText={setQuery}
          placeholder={searchPlaceholder}
          placeholderTextColor={palette.mutedForeground}
          autoCapitalize="none"
          autoCorrect={false}
          style={[styles.search, { borderColor: palette.border, backgroundColor: palette.background, color: palette.foreground, fontFamily: fontFamily("400") }]}
        />
        {full && fullNote && (
          <Txt size={12} muted style={{ paddingHorizontal: 8, marginBottom: 8 }}>
            {fullNote}
          </Txt>
        )}
        {shown.map((c) => {
          const on = isSelected(c.country);
          const blocked = full && !on;
          return (
            <Pressable
              key={c.country}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: on, disabled: blocked }}
              disabled={blocked}
              onPress={() => {
                onToggle(c.country);
                if (!multi) setOpen(false);
              }}
              style={({ pressed }) => [styles.row, pressed && { backgroundColor: palette.accent }, blocked && { opacity: 0.4 }]}
            >
              <View style={{ flex: 1, minWidth: 0 }}>
                <CountryBadge code={c.country} size={13} color={on ? palette.primary : palette.foreground} />
              </View>
              <Icon name="check" size={14} color={palette.primary} style={{ opacity: on ? 1 : 0 }} />
            </Pressable>
          );
        })}
        {shown.length === 0 && (
          <Txt size={12} muted style={{ paddingHorizontal: 8, paddingVertical: 12 }}>
            {noMatchLabel(query)}
          </Txt>
        )}
      </BottomSheet>
    </>
  );
}

const styles = StyleSheet.create({
  trigger: { flexDirection: "row", alignItems: "center", gap: 4, borderWidth: 1, borderStyle: "dashed", borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  search: { height: 36, borderWidth: 1, borderRadius: radius.md, paddingHorizontal: 12, fontSize: 13, marginHorizontal: 4, marginBottom: 8 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, paddingHorizontal: 8, paddingVertical: 8, borderRadius: radius.sm },
});
