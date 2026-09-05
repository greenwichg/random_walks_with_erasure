import * as React from "react";
import { useColorScheme } from "react-native";
import * as SecureStore from "expo-secure-store";

import { dark, light, type Palette } from "@/design/tokens";

/**
 * The theme, as the web resolves it.
 *
 * `next-themes` on the web keeps a preference of `light` / `dark` / `system` on the device and the
 * settings page writes the same value through to the account. This is the native half: the
 * preference is remembered on the device (so the app opens in the reader's theme before settings
 * load, and stays themed when signed out), `system` follows the OS, and the settings screen and the
 * header toggle both write here. The account write-through is theirs, exactly as on the web.
 */
export type ThemePreference = "light" | "dark" | "system";

interface ThemeValue {
  palette: Palette;
  scheme: "light" | "dark";
  preference: ThemePreference;
  setPreference: (next: ThemePreference) => void;
}

const KEY = "ih.theme";

const ThemeContext = React.createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const os = useColorScheme();
  const [preference, setPreferenceState] = React.useState<ThemePreference>("system");

  React.useEffect(() => {
    let live = true;
    SecureStore.getItemAsync(KEY)
      .then((v) => {
        if (live && (v === "light" || v === "dark" || v === "system")) setPreferenceState(v);
      })
      .catch(() => {
        /* an unreadable store is "system", not a crash */
      });
    return () => {
      live = false;
    };
  }, []);

  const setPreference = React.useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    SecureStore.setItemAsync(KEY, next).catch(() => {});
  }, []);

  const scheme: "light" | "dark" =
    preference === "system" ? (os === "dark" ? "dark" : "light") : preference;

  const value = React.useMemo<ThemeValue>(
    () => ({ palette: scheme === "dark" ? dark : light, scheme, preference, setPreference }),
    [scheme, preference, setPreference],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

/** The palette for the resolved scheme, plus the preference and its setter. */
export function useTheme(): ThemeValue {
  const ctx = React.useContext(ThemeContext);
  if (ctx) return ctx;
  // Outside the provider (a unit render, a test): light, and a setter that does nothing.
  return { palette: light, scheme: "light", preference: "system", setPreference: () => {} };
}
