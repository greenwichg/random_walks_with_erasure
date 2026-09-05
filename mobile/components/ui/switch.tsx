import * as React from "react";
import { Switch as RNSwitch } from "react-native";

import { useTheme } from "@/lib/theme";

/** `ui/switch.tsx`: the platform switch in the house colours (checked = primary, else `input`). */
export function Switch({
  checked,
  onChange,
  accessibilityLabel,
  disabled = false,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  accessibilityLabel?: string;
  disabled?: boolean;
}) {
  const { palette } = useTheme();
  return (
    <RNSwitch
      value={checked}
      onValueChange={onChange}
      disabled={disabled}
      accessibilityLabel={accessibilityLabel}
      trackColor={{ true: palette.primary, false: palette.input }}
      thumbColor={palette.background}
      ios_backgroundColor={palette.input}
    />
  );
}
