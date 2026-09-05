import * as React from "react";
import { StyleSheet, View } from "react-native";
import Svg, { Path } from "react-native-svg";

import { Txt } from "@/components/ui/text";
import { useTheme } from "@/lib/theme";

/** The Hidden View mark — a pulse inside a rounded shield — and the wordmark beside it. */
export function Logo() {
  const { palette } = useTheme();
  return (
    <View style={styles.row}>
      <View style={[styles.mark, { backgroundColor: palette.primary, shadowColor: palette.primary }]}>
        <Svg viewBox="0 0 24 24" width={18} height={18} fill="none">
          <Path
            d="M3 12h3.5l2-5 3 10 2.5-7 1.5 2H21"
            stroke={palette.primaryForeground}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </Svg>
      </View>
      <Txt size={15} weight="600" tight>
        Hidden <Txt size={15} weight="600" tight color={palette.primary}>View</Txt>
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  mark: {
    width: 32,
    height: 32,
    borderRadius: 11,
    alignItems: "center",
    justifyContent: "center",
    shadowOpacity: 0.35,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
});
