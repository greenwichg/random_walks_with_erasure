import * as React from "react";
import { View } from "react-native";

import { Txt } from "@/components/ui/text";
import { alpha, radius } from "@/design/tokens";
import { useTheme } from "@/lib/theme";

/** What a breakdown tab says when the story's data cannot back it: one sentence, never an empty panel. */
export function EmptyBreakdown({ children }: { children: string }) {
  const { palette } = useTheme();
  return (
    <View style={{ backgroundColor: alpha(palette.muted, 0.5), borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 24 }}>
      <Txt size={12} muted align="center" lineHeight={18}>
        {children}
      </Txt>
    </View>
  );
}
