import * as React from "react";
import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import { Card } from "@/components/ui/card";
import { Txt } from "@/components/ui/text";

import { InfoTooltip } from "./info-tooltip";

/** A titled card with optional info tooltip + header action — used across pages. */
export function SectionCard({
  title,
  info,
  action,
  children,
  style,
}: {
  title: string;
  info?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  return (
    <Card style={style}>
      <View style={styles.header}>
        <View style={styles.title}>
          <Txt size={14} weight="600">
            {title}
          </Txt>
          {info && <InfoTooltip text={info} />}
        </View>
        {action}
      </View>
      {children}
    </Card>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 12 },
  title: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 },
});
