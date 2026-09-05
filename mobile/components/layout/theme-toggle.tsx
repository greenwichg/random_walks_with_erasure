import * as React from "react";

import { Button } from "@/components/ui/button";
import { useUpdateSettings } from "@/lib/hooks";
import { useAuth } from "@/lib/auth-context";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

/**
 * Light/dark toggle. Applies at once on this device and, as on the web's settings page, writes
 * through to the account so the reader's other devices follow — never via a Save button.
 */
export function ThemeToggle() {
  const { t } = useTranslation();
  const { scheme, setPreference, palette } = useTheme();
  const { signedIn } = useAuth();
  const persist = useUpdateSettings();
  const next = scheme === "dark" ? "light" : "dark";
  return (
    <Button
      variant="ghost"
      size="icon"
      icon={scheme === "dark" ? "sun" : "moon"}
      textColor={palette.mutedForeground}
      accessibilityLabel={t("common.toggleTheme")}
      onPress={() => {
        setPreference(next);
        if (signedIn) persist.mutate({ theme: next });
      }}
    />
  );
}
