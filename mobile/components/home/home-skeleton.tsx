import * as React from "react";
import { View } from "react-native";

import { Skeleton } from "@/components/ui/skeleton";

/** The home page's loading shape: briefing card, lens strip, lead, then three rows with thumbs. */
export function HomeSkeleton() {
  return (
    <View style={{ gap: 16 }} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <Skeleton height={144} />
      <Skeleton height={36} width="70%" />
      <Skeleton style={{ aspectRatio: 16 / 9, width: "100%" }} />
      <Skeleton height={26} width="85%" />
      {Array.from({ length: 3 }).map((_, i) => (
        <View key={i} style={{ flexDirection: "row", gap: 12 }}>
          <Skeleton height={80} style={{ flex: 1 }} />
          <Skeleton height={88} width={88} />
        </View>
      ))}
    </View>
  );
}
