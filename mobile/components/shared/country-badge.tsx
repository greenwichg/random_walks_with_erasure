import * as React from "react";
import { StyleSheet, View } from "react-native";
import { SvgUri } from "react-native-svg";

import { countryFlagSrc, countryName, countryShortName } from "@ih/core/logic/countries";

import { Txt } from "@/components/ui/text";
import { config } from "@/lib/config";
import { useTranslation } from "@/lib/i18n-context";

/**
 * A country rendered for humans: decorative flag + the localized display name, from nothing but
 * the canonical ISO code. The flag is an IMAGE, not an emoji, for the reason the web states (no
 * flag glyphs on some platforms) — the same MIT flag-icons artwork the deployment serves under
 * `/flags`, so both clients show the identical chip. A code with no artwork degrades to the name.
 *
 * A phone is a narrow screen, so the SHORT form ("USA") is what renders — the web's `sm:hidden`
 * branch — while the accessible name stays the full one.
 */
export function CountryBadge({ code, size = 12, color }: { code: string; size?: number; color?: string }) {
  const { lang } = useTranslation();
  const [broken, setBroken] = React.useState(false);
  const path = countryFlagSrc(code);
  const full = countryName(code, lang);
  const short = countryShortName(code, lang);
  const uri = path && config.apiBaseUrl ? `${config.apiBaseUrl}${path}` : null;

  return (
    <View style={styles.row} accessible accessibilityLabel={full}>
      {uri && !broken && (
        <View style={styles.flag}>
          <SvgUri uri={uri} width={16} height={12} onError={() => setBroken(true)} />
        </View>
      )}
      <Txt size={size} color={color} lineHeight={Math.round(size * 1.3)}>
        {short}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 6 },
  flag: { width: 16, height: 12, borderRadius: 2, overflow: "hidden" },
});
